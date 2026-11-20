"""Real open-license eye / retina sample photographs from Wikimedia Commons.

Committed in samples/ (re-downloaded only if missing), resized to max 768x768, JPEG.
Saved results live in samples/results/<id>.json, recorded from real live runs.
"""
import io
import json
import logging
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger("retinaguard.samples")

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
RESULTS_DIR = SAMPLES_DIR / "results"
MAX_SIDE = 768
# Wikimedia rejects generic browser UAs with 403; a descriptive client UA is accepted.
UA = "RetinaGuardDemo/1.0 (https://example.org/retinaguard)"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}"

SAMPLES = [
    {
        "id": "healthy_fundus", "name": "Healthy Retinal Fundus",
        "commons": "Fundus_photograph_of_normal_right_eye.jpg",
        "ctx": {"label": "DEMO-HEALTHY", "age": 44, "dur": 3, "hba": 6.2, "sym": ""},
    },
    {
        "id": "diabetic_retinopathy", "name": "Diabetic Retinopathy Fundus",
        "commons": "Fundus_photo_showing_scatter_laser_surgery_for_diabetic_retinopathy_EDA09.JPG",
        "ctx": {"label": "DEMO-DR", "age": 61, "dur": 14, "hba": 9.1, "sym": "Blurred vision"},
    },
    {
        "id": "anterior_eye_cataract", "name": "Anterior Eye · Cataract",
        "commons": "Cataract_in_human_eye.png",
        "ctx": {"label": "DEMO-CATARACT", "age": 67, "dur": 9, "hba": 7.4, "sym": "Glare"},
    },
]


def _fetch(name: str) -> bytes:
    r = httpx.get(FILEPATH.format(name=name), params={"width": 800}, headers={"User-Agent": UA},
                  follow_redirects=True, timeout=30.0)
    r.raise_for_status()
    return r.content


def ensure_samples():
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for old in SAMPLES_DIR.glob("*.png"):  # legacy synthetic samples
        old.unlink(missing_ok=True)
    for s in SAMPLES:
        p = SAMPLES_DIR / f"{s['id']}.jpg"
        if p.exists():
            continue
        try:
            im = Image.open(io.BytesIO(_fetch(s["commons"]))).convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            im.save(p, "JPEG", quality=88)
        except Exception as e:  # offline start must not crash the app
            log.warning("Could not download sample %s: %s", s["id"], e)


def _by_id(sid: str):
    return next((s for s in SAMPLES if s["id"] == sid), None)


def saved_result(sid: str):
    p = RESULTS_DIR / f"{sid}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _public(s):
    return {"id": s["id"], "title": s["name"], "name": s["name"], "ctx": s["ctx"],
            "input_url": f"samples/{s['id']}.jpg", "has_saved_result": (RESULTS_DIR / f"{s['id']}.json").exists()}


def available_samples():
    return [_public(s) for s in SAMPLES if (SAMPLES_DIR / f"{s['id']}.jpg").exists()]


def sample_with_result(sid: str):
    s = _by_id(sid)
    if not s or not (SAMPLES_DIR / f"{s['id']}.jpg").exists():
        return None
    return _public(s) | {"result": saved_result(sid)}
