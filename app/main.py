"""RetinaGuard — FastAPI app. Serves the studio UI directly at / (no landing page)."""
import asyncio
import base64
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from . import db  # noqa: E402
from .pipeline import run_pipeline  # noqa: E402
from .providers import models  # noqa: E402
from .samples import available_samples, ensure_samples  # noqa: E402

MAX_BYTES = 12 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    await asyncio.to_thread(ensure_samples)
    yield


app = FastAPI(title="RetinaGuard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


def _keys():
    return {p: db.resolve_key(p)[0] for p in ("groq",)}


def _mask(k):
    return None if not k else (k[:4] + "…" + k[-4:] if len(k) > 10 else "••••")


def fmt_wait(s: int) -> str:
    return f"{s // 60}m {s % 60:02d}s"


# ---------- status & keys ----------
@app.get("/api/status")
async def status():
    return {"rate": db.rate_status(), "keys": _key_info(), "models": models()}


def _key_info():
    out = {}
    for p in ("groq",):
        k, src = db.resolve_key(p)
        out[p] = {"set": bool(k), "source": src, "masked": _mask(k)}
    return out


class KeysIn(BaseModel):
    groq: str | None = None


@app.post("/api/keys")
async def save_keys(body: KeysIn):
    for p in ("groq",):
        v = (getattr(body, p) or "").strip()
        if v:
            db.set_setting(f"{p}_key", v)
    return _key_info()


@app.delete("/api/keys/{provider}")
async def clear_key(provider: str):
    if provider != "groq":
        raise HTTPException(404)
    db.set_setting(f"{provider}_key", "")
    return _key_info()


@app.get("/api/samples")
async def samples():
    return available_samples()


# ---------- screening ----------
def _prepare(data: bytes):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "Unsupported or corrupt image. Please upload a JPG, PNG or WEBP file.")
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


@app.post("/api/screen")
async def screen(
    file: UploadFile = File(...),
    modality: str = Form("auto"),
    label: str = Form(""),
    age: str = Form(""),
    diabetes_years: str = Form(""),
    hba1c: str = Form(""),
    symptoms: str = Form(""),
):
    keys = _keys()
    if not keys["groq"]:
        raise HTTPException(400, "No API key configured. Click 'API Keys' and add a free Groq key (gsk_...).")
    rl = db.rate_status()
    if rl["remaining"] <= 0:
        raise HTTPException(
            429,
            f"Demo limit reached: {rl['limit']} screenings per hour. Next slot opens in {fmt_wait(rl['reset_in'])}.",
            headers={"Retry-After": str(rl["reset_in"])},
        )
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image too large (max 12 MB).")
    image, thumb = _prepare(data)
    hint = {"auto": "Auto-Detect", "fundus": "Retinal Fundus Scan", "anterior": "Anterior / External Eye Photo"}.get(modality, "Auto-Detect")
    patient = {"label": label.strip()[:80], "age": age.strip()[:6], "diabetes_years": diabetes_years.strip()[:6],
               "hba1c": hba1c.strip()[:6], "symptoms": symptoms.strip()[:200]}

    async def stream():
        async for ev in run_pipeline(keys, image, thumb, patient, hint):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- history & compare ----------
@app.get("/api/history")
async def history():
    return db.list_screenings()


@app.get("/api/history/{sid}")
async def history_item(sid: int):
    r = db.get_screening(sid)
    if not r:
        raise HTTPException(404, "Screening not found")
    return r


@app.delete("/api/history/{sid}")
async def history_delete(sid: int):
    db.delete_screening(sid)
    return {"ok": True}


@app.get("/api/compare")
async def compare(a: int, b: int):
    ra, rb = db.get_screening(a), db.get_screening(b)
    if not ra or not rb:
        raise HTTPException(404, "Screening not found")
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
