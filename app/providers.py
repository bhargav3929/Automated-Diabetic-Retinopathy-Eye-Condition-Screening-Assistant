"""Direct REST client for Groq (vision). No SDKs, no C-extensions.

call_model() tries the primary Groq vision model first, then falls back to the next model in the chain
on any failure (model unavailable, 429/5xx, timeout, bad JSON).
"""
import asyncio
import base64
import json
import os
import re
import time

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
]


class LLMError(Exception):
    pass


class _HTTP(Exception):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body}")
        self.code = code


def model_chain() -> list[str]:
    first = os.getenv("GROQ_MODEL", "").strip()
    return ([first] if first else []) + [m for m in DEFAULT_MODELS if m != first]


def models():
    return {"groq": model_chain()[0]}


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


async def call_model(keys: dict, system: str, prompt: str, image: bytes | None = None, json_mode: bool = True,
                     timeout: float = 20.0, max_tokens: int = 1000):
    """Returns (result, meta). Groq only; falls back through the vision model chain on failure."""
    key = keys.get("groq")
    if not key:
        raise LLMError("No Groq API key configured. Open 'API Keys' and add a Groq key (gsk_...).")
    errors = []
    for model in model_chain():
        t0 = time.perf_counter()
        try:
            text = await _groq(key, model, system, prompt, image, json_mode, timeout, max_tokens)
            out = parse_json(text) if json_mode else _strip_think(text)
            return out, {"provider": "Groq", "model": model, "latency_ms": int((time.perf_counter() - t0) * 1000)}
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            errors.append(f"{model}: timed out after {timeout:.0f}s")
        except Exception as e:
            errors.append(f"{model}: {type(e).__name__} {e}")
    raise LLMError("Groq failed. " + " | ".join(errors))
