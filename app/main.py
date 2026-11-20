"""RetinaGuard - FastAPI app. Serves the studio UI directly at / (no landing page)."""
import asyncio
import base64
import io
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db
from .config import ROOT
from .errors import AppError
from .pipeline import run_pipeline
from .providers import models
from .samples import SAMPLES_DIR, available_samples, ensure_samples, sample_with_result
from .demo_auth import verify_ticket

@asynccontextmanager
async def lifespan(_: FastAPI):
    import logging
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    if config.DEMO_MODE and not config.DEMO_TICKET_SECRET:
        logging.getLogger("retinaguard").warning("DEMO_MODE is on but DEMO_TICKET_SECRET is empty: every ticket will be invalid.")
    await asyncio.to_thread(ensure_samples)
    yield


app = FastAPI(title="RetinaGuard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")


# ---------- errors: always {"error", "code"} ----------
def _err(status, code, message, headers=None):
    return JSONResponse({"error": message, "code": code}, status_code=status, headers=headers)


@app.exception_handler(AppError)
async def _app_error(_, e: AppError):
    return _err(e.status, e.code, e.message, e.headers)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_, e: StarletteHTTPException):
    code = {404: "NOT_FOUND", 413: "TOO_LARGE"}.get(e.status_code, "BAD_INPUT" if e.status_code < 500 else "AI_FAILED")
    return _err(e.status_code, code, str(e.detail) if e.status_code != 404 else "Not found.")


@app.exception_handler(RequestValidationError)
async def _invalid(_, e):
    return _err(400, "BAD_INPUT", "Some required fields are missing or invalid.")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- demo ticket helpers ----------
def _ticket(request: Request):
    """(payload, error_code) from the X-LastSem-Ticket header."""
    return verify_ticket(request.headers.get("x-lastsem-ticket"), config.DEMO_TICKET_SECRET, config.PROJECT_SLUG)


def _owner(request: Request) -> str:
    """History owner: '' for the local version, the ticket id in demo mode ('?' = no valid ticket)."""
    if not config.DEMO_MODE:
        return ""
    payload, _err_code = _ticket(request)
    return payload["tid"] if payload else "?"


def _mask(k):
    return None if not k else (k[:4] + "…" + k[-4:] if len(k) > 10 else "••••")


def _key_info():
    k, src = db.resolve_key("groq")
    return {"groq": {"configured": bool(k), "source": src, "masked": _mask(k)}}


def _require_keys_enabled():
    if config.DEMO_MODE:
        raise AppError("NOT_FOUND", "Not found.")


def _require_local(request: Request):
    _require_keys_enabled()
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1") or "x-forwarded-for" in request.headers or "forwarded" in request.headers:
        raise AppError("KEYS_LOCAL_ONLY", "API keys can only be changed from the computer running this app.")


# ---------- info & keys ----------
@app.get("/api/info")
async def info(request: Request):
    key, _src = db.resolve_key("groq")
    demo = None
    if config.DEMO_MODE:
        payload, _c = _ticket(request)
        left = max(0, int(payload.get("runs", 0)) - db.ticket_runs_used(payload["tid"])) if payload else 0
        demo = {"ticket_valid": bool(payload), "runs_left": left, "daily_cap_reached": db.runs_today() >= config.DEMO_DAILY_CAP}
    return {
        "name": config.PROJECT_NAME, "slug": config.PROJECT_SLUG, "number": config.PROJECT_NUMBER,
        "version": config.VERSION, "mode": "demo" if config.DEMO_MODE else "local",
        "ai": {"provider": "groq", "ready": bool(key), "model": models()["groq"]},
        "limits": {"max_upload_mb": config.DEMO_MAX_UPLOAD_MB if config.DEMO_MODE else config.MAX_UPLOAD_MB},
        "demo": demo,
    }


@app.get("/api/status")
async def status(request: Request):
    return {"rate": db.rate_status(), "keys": _key_info() if not config.DEMO_MODE else {"groq": {"configured": True, "source": "env", "masked": None}},
            "models": models(), "mode": "demo" if config.DEMO_MODE else "local"}


class KeysIn(BaseModel):
    groq_api_key: str | None = None


@app.get("/api/keys")
async def get_keys():
    _require_keys_enabled()
    return _key_info()


@app.post("/api/keys")
async def save_keys(body: KeysIn, request: Request):
    _require_local(request)
    v = (body.groq_api_key or "").strip()
    if not config.usable_key(v):
        raise AppError("BAD_INPUT", "That doesn't look like a Groq key. It should start with gsk_ and have no spaces.")
    db.set_setting("groq_key", v)
    return {"saved": ["groq"]}


@app.delete("/api/keys/groq")
async def clear_key(request: Request):
    _require_local(request)
    db.set_setting("groq_key", "")
    return {"removed": "groq"}


# ---------- samples ----------
@app.get("/api/samples")
async def samples():
    return {"samples": available_samples()}


@app.get("/api/samples/{sid}")
async def sample(sid: str):
    s = sample_with_result(sid)
    if not s:
        raise AppError("NOT_FOUND", "Unknown sample.")
    return s


# ---------- screening ----------
def _prepare(data: bytes):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise AppError("BAD_INPUT", "Unsupported or corrupt image. Please upload a JPG, PNG or WEBP file.")
    if im.mode != "RGB":
        im = im.convert("RGB")
    big = im.copy()
    big.thumbnail((768, 768))
    b = io.BytesIO()
    big.save(b, "JPEG", quality=82)
    th = im.copy()
    th.thumbnail((360, 360))
    t = io.BytesIO()
    th.save(t, "JPEG", quality=78)
    return b.getvalue(), "data:image/jpeg;base64," + base64.b64encode(t.getvalue()).decode()


def fmt_wait(s: int) -> str:
    return f"{s // 60}m {s % 60:02d}s"


@app.post("/api/screen")
async def screen(
    request: Request,
    file: UploadFile = File(...),
    modality: str = Form("auto"),
    label: str = Form(""),
    age: str = Form(""),
    diabetes_years: str = Form(""),
    hba1c: str = Form(""),
    symptoms: str = Form(""),
):
    tid = None
    if config.DEMO_MODE:  # live runs need a valid ticket with runs left, and the global daily cap
        payload, code = _ticket(request)
        if code:
            raise AppError(code)
        tid = payload["tid"]
        if db.ticket_runs_used(tid) >= int(payload.get("runs", 0)):
            raise AppError("TICKET_USED_UP")
        if db.runs_today() >= config.DEMO_DAILY_CAP:
            raise AppError("DAILY_CAP_REACHED")
    key, _src = db.resolve_key("groq")
    if not key:
        raise AppError("AI_NOT_CONFIGURED")
    keys = {"groq": key}
    rl = db.rate_status()
    if rl["remaining"] <= 0:
        raise AppError("RATE_LIMITED", f"Demo limit reached: {rl['limit']} screenings per hour. Next slot opens in {fmt_wait(rl['reset_in'])}.",
                       headers={"Retry-After": str(rl["reset_in"])})
    limit = config.max_upload_bytes()
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("TOO_LARGE", f"Image too large (max {limit // (1024 * 1024)} MB).")
    if not data:
        raise AppError("BAD_INPUT", "The uploaded file is empty.")
    image, thumb = _prepare(data)
    hint = {"auto": "Auto-Detect", "fundus": "Retinal Fundus Scan", "anterior": "Anterior / External Eye Photo"}.get(modality, "Auto-Detect")
    patient = {"label": label.strip()[:80], "age": age.strip()[:6], "diabetes_years": diabetes_years.strip()[:6],
               "hba1c": hba1c.strip()[:6], "symptoms": symptoms.strip()[:200]}

    async def stream():
        async for ev in run_pipeline(keys, image, thumb, patient, hint, owner=tid or "",
                                     on_success=(lambda: db.record_ticket_run(tid)) if tid else None):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- history & compare ----------
@app.get("/api/history")
async def history(request: Request):
    return db.list_screenings(_owner(request))


@app.get("/api/history/{sid}")
async def history_item(sid: int, request: Request):
    r = db.get_screening(sid, _owner(request))
    if not r:
        raise AppError("NOT_FOUND", "Screening not found.")
    return r


@app.delete("/api/history/{sid}")
async def history_delete(sid: int, request: Request):
    db.delete_screening(sid, _owner(request))
    return {"ok": True}


@app.get("/api/compare")
async def compare(a: int, b: int, request: Request):
    o = _owner(request)
    ra, rb = db.get_screening(a, o), db.get_screening(b, o)
    if not ra or not rb:
        raise AppError("NOT_FOUND", "Screening not found.")
    base, follow = (ra, rb) if ra["created_at"] <= rb["created_at"] else (rb, ra)
    gb, gf = base["grading"], follow["grading"]
    same_kind = gb["kind"] == gf["kind"]
    sd = (gf["stage"] or 0) - (gb["stage"] or 0) if same_kind else None
    trend = "not comparable (different scan modality)" if sd is None else ("progression" if sd > 0 else "improvement" if sd < 0 else "stable")
    lesions = []
    bm = {i["name"]: i for i in base["biomarkers"]["items"]}
    order = ["None", "Mild", "Moderate", "Severe"]
    for i in follow["biomarkers"]["items"]:
        o = bm.get(i["name"])
        if not o:
            continue
        if o["present"] != i["present"] or o["severity"] != i["severity"]:
            change = ("new" if i["present"] and not o["present"] else "resolved" if o["present"] and not i["present"]
                      else "worsened" if order.index(i["severity"]) > order.index(o["severity"]) else "improved")
            lesions.append({"name": i["name"], "before": o["severity"] if o["present"] else "Absent",
                            "after": i["severity"] if i["present"] else "Absent", "change": change})
    tb, tf = base["triage"]["urgency_index"], follow["triage"]["urgency_index"]
    return {
        "baseline": base, "followup": follow,
        "delta": {
            "stage_delta": sd, "trend": trend, "same_modality": same_kind, "lesions": lesions,
            "urgency_change": {"before": base["triage"]["urgency_tier"], "after": follow["triage"]["urgency_tier"], "shift": tf - tb},
            "confidence_delta": round(gf["confidence"] - gb["confidence"], 1),
            "interval_days": round((follow["created_at"] - base["created_at"]) / 86400, 1),
            "edema": {"before": gb["macular_edema"], "after": gf["macular_edema"]},
        },
    }
