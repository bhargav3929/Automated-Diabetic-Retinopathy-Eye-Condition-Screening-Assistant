"""Settings read from environment / .env, each with a default so a missing line never crashes the app."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PLACEHOLDER = "your_groq_key_here"


def _s(name, default=""):
    return os.getenv(name, default).strip()


def _i(name, default):
    try:
        return int(_s(name, str(default)))
    except ValueError:
        return default


def _b(name, default=False):
    v = _s(name).lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default


VERSION = "1.0.0"
PROJECT_NAME = _s("PROJECT_NAME", "RetinaGuard")
PROJECT_SLUG = _s("PROJECT_SLUG", "retinaguard")
PROJECT_NUMBER = _i("PROJECT_NUMBER", 2)
HOST = _s("HOST", "127.0.0.1")
PORT = _i("PORT", 8002)
DEMO_MODE = _b("DEMO_MODE")
DEMO_TICKET_SECRET = _s("DEMO_TICKET_SECRET")
DEMO_DAILY_CAP = _i("DEMO_DAILY_CAP", 200)
TRUST_PROXY = _b("TRUST_PROXY")
MAX_UPLOAD_MB = _i("MAX_UPLOAD_MB", 12)
DEMO_MAX_UPLOAD_MB = _i("DEMO_MAX_UPLOAD_MB", 3)
GROQ_MODEL = _s("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = _s("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")
GROQ_VISION_MODEL = _s("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
GROQ_VISION_FALLBACK_MODEL = _s("GROQ_VISION_FALLBACK_MODEL", "qwen/qwen3.8-27b")


def max_upload_bytes() -> int:
    return (DEMO_MAX_UPLOAD_MB if DEMO_MODE else MAX_UPLOAD_MB) * 1024 * 1024


def usable_key(k) -> bool:
    k = (k or "").strip()
    return bool(k) and k != PLACEHOLDER and " " not in k and k.startswith("gsk_")
