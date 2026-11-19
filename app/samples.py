"""Real open-license eye / retina sample photographs from Wikimedia Commons.

Downloaded once (if missing), resized to max 768x768 and stored as JPEG in static/samples/.
"""
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger("retinaguard.samples")

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "static" / "samples"
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


def available_samples():
    return [{k: s[k] for k in ("id", "name", "ctx")} | {"url": f"/static/samples/{s['id']}.jpg"}
            for s in SAMPLES if (SAMPLES_DIR / f"{s['id']}.jpg").exists()]
