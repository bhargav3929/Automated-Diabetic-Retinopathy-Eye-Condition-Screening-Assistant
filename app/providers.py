"""Direct REST clients for Gemini (primary) and Groq (hedge). No SDKs, no C-extensions.

call_model() uses hedged racing: Gemini starts first; if it errors (429/503/504/...) or takes longer than
HEDGE_AFTER seconds, Groq is launched in parallel and whichever answers first wins. A provider that fails
is skipped for BREAKER_SECONDS (circuit breaker).
"""
import asyncio
import base64
import json
import os
import re
import time

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HEDGE_AFTER = 5.0
BREAKER_SECONDS = 120.0
_broken_until: dict[str, float] = {}


class LLMError(Exception):
    pass


class _HTTP(Exception):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body}")
        self.code = code


def models():
    return {
        "gemini": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        "groq": os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
    }


def _strip_think(t: str) -> str:
    return re.sub(r"<think>.*?</think>", "", t or "", flags=re.S).strip()


def parse_json(text: str) -> dict:
    t = _strip_think(text)
    fence = re.match(r"^```(?:json|JSON)?\s*(.*?)\s*```$", t, flags=re.S)
    if fence:
        t = fence.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(t[a : b + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError("Model returned non-JSON output")


async def _gemini(key, model, system, prompt, image, json_mode, timeout, max_tokens):
    parts = [{"text": prompt}]
    if image:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image).decode()}})
    gen = {"temperature": 0.2, "maxOutputTokens": max_tokens}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if r.status_code != 200:
        raise _HTTP(r.status_code, r.text[:300])
    d = r.json()
    try:
        return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"Gemini returned no content: {str(d)[:200]}")


async def _groq(key, model, system, prompt, image, json_mode, timeout, max_tokens):
    if image:
        user = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}}]
    else:
        user = prompt
    body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise _HTTP(r.status_code, r.text[:300])
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError("Groq returned no content")


async def _run(provider, keys, system, prompt, image, json_mode, timeout, max_tokens):
    """One provider attempt -> (result, meta). Trips the circuit breaker on failure."""
    m = models()
    t0 = time.perf_counter()
    try:
        if provider == "gemini":
            text = await _gemini(keys["gemini"], m["gemini"], system, prompt, image, json_mode, timeout, max_tokens)
            model = m["gemini"]
        else:
            text = await _groq(keys["groq"], m["groq"], system, prompt, image, json_mode, timeout, max_tokens)
            model = m["groq"]
        out = parse_json(text) if json_mode else _strip_think(text)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _broken_until[provider] = time.monotonic() + BREAKER_SECONDS
        if isinstance(e, httpx.TimeoutException):
            raise LLMError(f"{provider.title()} timed out after {timeout:.0f}s")
        raise LLMError(f"{provider.title()}: {type(e).__name__} {e}")
    return out, {"provider": provider.title(), "model": model, "latency_ms": int((time.perf_counter() - t0) * 1000)}


def _order(keys):
    avail = [p for p in ("gemini", "groq") if keys.get(p)]
    now = time.monotonic()
    healthy = [p for p in avail if _broken_until.get(p, 0) <= now]
    return healthy or avail  # if everything is tripped, try anyway


async def call_model(keys: dict, system: str, prompt: str, image: bytes | None = None, json_mode: bool = True,
                     timeout: float = 20.0, max_tokens: int = 1000, hedge_after: float = HEDGE_AFTER):
    """Returns (result, meta). Gemini first; Groq joins the race on error or after `hedge_after` seconds."""
    order = _order(keys)
    if not order:
        raise LLMError("No API key configured. Open 'API Keys' and add a Gemini or Groq key.")

    def start(p):
        return asyncio.create_task(_run(p, keys, system, prompt, image, json_mode, timeout, max_tokens))

    tasks = {start(order[0]): order[0]}
    pending_second = order[1:]
    errors = []
    try:
        while tasks:
            wait_for = hedge_after if pending_second else None
            done, _ = await asyncio.wait(tasks, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            if not done:  # primary is slow -> hedge
                p = pending_second.pop(0)
                tasks[start(p)] = p
                continue
            for t in done:
                p = tasks.pop(t)
                try:
                    return t.result()
                except LLMError as e:
                    errors.append(str(e))
                    if pending_second:  # primary failed fast -> launch the hedge immediately
                        q = pending_second.pop(0)
                        tasks[start(q)] = q
        raise LLMError("All providers failed. " + " | ".join(errors))
    finally:
        for t in tasks:
            t.cancel()
