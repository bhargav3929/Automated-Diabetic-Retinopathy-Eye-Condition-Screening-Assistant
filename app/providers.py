"""Direct REST client for Groq. No SDKs, no C-extensions. All AI access goes through call_model().

Every call has a timeout, retries 429/5xx/timeouts with exponential backoff, falls back to the fallback
model, and turns failures into plain-English AppErrors (never the raw provider text or the key).
"""
import asyncio
import base64
import json
import logging
import random
import re
import time

import httpx

from . import config
from .errors import AppError

log = logging.getLogger("retinaguard.ai")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
RETRY_CODES = {429, 500, 502, 503, 504}
ATTEMPTS = 3
LLMError = AppError  # pipeline code catches this name


class _HTTP(Exception):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body}")
        self.code, self.body = code, body


def model_chain(vision: bool = True) -> list[str]:
    a, b = (config.GROQ_VISION_MODEL, config.GROQ_VISION_FALLBACK_MODEL) if vision else (config.GROQ_MODEL, config.GROQ_FALLBACK_MODEL)
    return [a] if a == b or not b else [a, b]


def models():
    return {"groq": model_chain()[0]}


def _redact(t) -> str:
    return re.sub(r"gsk_[A-Za-z0-9]+", "gsk_***", str(t))


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
        raise ValueError("non-JSON output")


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
        return r.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError):
        raise ValueError("no content")


def _wait_seconds(err, attempt: int) -> float:
    if isinstance(err, _HTTP) and err.code == 429:
        m = re.search(r"try again in ([\d.]+)\s*(ms|s)", err.body or "", re.I)
        if m:
            x = float(m.group(1)) / (1000 if m.group(2).lower() == "ms" else 1)
            if x <= 20:
                return x
    return (2 ** attempt) + random.random() * 0.5


async def call_model(keys: dict, system: str, prompt: str, image: bytes | None = None, json_mode: bool = True,
                     timeout: float = 60.0, max_tokens: int = 1000):
    """Returns (result, meta). Raises AppError with a plain message on failure."""
    key = keys.get("groq")
    if not config.usable_key(key):
        raise AppError("AI_NOT_CONFIGURED")
    saw_key_error = False
    for model in model_chain(vision=bool(image)):
        for attempt in range(ATTEMPTS):
            t0 = time.perf_counter()
            try:
                text = await _groq(key, model, system, prompt, image, json_mode, timeout, max_tokens)
                out = parse_json(text) if json_mode else _strip_think(text)
                return out, {"provider": "Groq", "model": model, "latency_ms": int((time.perf_counter() - t0) * 1000)}
            except asyncio.CancelledError:
                raise
            except _HTTP as e:
                log.warning("Groq %s HTTP %s: %s", model, e.code, _redact(e.body))
                if e.code == 401:
                    saw_key_error = True
                    break
                if e.code not in RETRY_CODES:
                    break
                if attempt < ATTEMPTS - 1:
                    await asyncio.sleep(_wait_seconds(e, attempt))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                log.warning("Groq %s network error: %s", model, type(e).__name__)
                if attempt < ATTEMPTS - 1:
                    await asyncio.sleep(_wait_seconds(e, attempt))
            except ValueError as e:
                log.warning("Groq %s bad output: %s", model, e)
                if attempt >= 1:  # invalid output: retry once only
                    break
            except Exception as e:
                log.warning("Groq %s failed: %s", model, _redact(e))
                break
        if saw_key_error:
            break
    raise AppError("AI_KEY_INVALID" if saw_key_error else "AI_BUSY")
