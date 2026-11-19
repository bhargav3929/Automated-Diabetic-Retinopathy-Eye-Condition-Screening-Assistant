"""5-node clinical triage pipeline (pure Python). Nodes 2-4 run as ONE consolidated AI call; node 1 is a fast gatekeeper call."""
import asyncio
import io
import time
from datetime import datetime

from PIL import Image

from . import db
from .providers import LLMError, call_model

DISCLAIMER = (
    "Preliminary AI screening decision-support report — not a standalone medical diagnosis; "
    "confirm via dilated slit-lamp/ophthalmic exam."
)

FUNDUS_MARKERS = [
    "Microaneurysms", "Dot-Blot Hemorrhages", "Hard Exudates",
    "Cotton-Wool Spots", "Neovascularization", "Optic Disc Cup-to-Disc Ratio",
]
ANTERIOR_MARKERS = ["Lens Opacity / Cataract Haze", "Conjunctival Hyperemia", "Pterygium Growth", "Corneal Opacity"]

ICDR = {
    0: "Stage 0: No Apparent DR", 1: "Stage 1: Mild NPDR", 2: "Stage 2: Moderate NPDR",
    3: "Stage 3: Severe NPDR", 4: "Stage 4: Proliferative DR",
}
ANTERIOR_SEV = ["Normal", "Mild", "Moderate", "Urgent"]
TIERS = [
    "Routine 12-Month Screening",
    "Ophthalmology Consult within 3 Months",
    "Urgent Retina/Cornea Specialist Referral within 2–4 Weeks",
    "Immediate Same-Week Ophthalmic Referral",
]
SEVERITY_LEVELS = ["None", "Mild", "Moderate", "Severe"]
SYSTEM = (
    "You are an ophthalmic screening assistant supporting a clinician. You analyze images and structured data "
    "and respond ONLY with a single valid JSON object that follows the requested schema. No prose, no markdown."
)


def _ctx_text(p: dict) -> str:
    parts = []
    for k, label in (("age", "Age"), ("diabetes_years", "Diabetes duration (yrs)"), ("hba1c", "HbA1c %"), ("symptoms", "Reported symptoms")):
        if p.get(k) not in (None, ""):
            parts.append(f"{label}: {p[k]}")
    return "; ".join(parts) or "No patient context provided"


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "present", "1")
    return bool(v)


def _clamp_int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _conf(v):
    try:
        f = float(str(v).strip("% "))
    except (TypeError, ValueError):
        return 60.0
    if 0 < f <= 1:
        f *= 100
    return round(max(0.0, min(100.0, f)), 1)


def _pick(v, options, default):
    s = str(v or "").strip().lower()
    for o in options:
        if o.lower() == s:
            return o
    for o in options:
        if s and o.lower() in s:
            return o
    return default




# ---------------- Node 1: Gatekeeper (fast 384px check) ----------------
def _small(image: bytes, side: int = 384) -> bytes:
    im = Image.open(io.BytesIO(image)).convert("RGB")
    im.thumbnail((side, side))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


async def node1(keys, image, modality_pref):
    prompt = f"""Ocular Gatekeeper. Is this a real photograph of an eye? ACCEPT any retinal fundus photograph (any colour, any disease,
any quality, with or without laser scars) and any external/anterior eye photograph (slit-lamp, close-up, cataract, redness, with or without eyelids).
Also accept clear ophthalmic diagrams. REJECT ONLY clearly non-eye content (objects, plants, documents, screenshots, faces with no eye focus).
When unsure, ACCEPT. Hint from user: {modality_pref}.
JSON only: {{"is_ocular":true|false,"modality":"Retinal Fundus"|"Anterior Eye"|"None","gradability":"Adequate"|"Borderline"|"Ungradable","reason":"max 12 words"}}"""
    d, meta = await call_model(keys, SYSTEM, prompt, _small(image), timeout=10.0, max_tokens=120)
    modality = _pick(d.get("modality"), ["Retinal Fundus", "Anterior Eye", "None"], "None")
    ocular = _bool(d.get("is_ocular"))
    if ocular and modality == "None":  # model said eye but no modality: use the user's hint, else fundus
        modality = "Anterior Eye" if "Anterior" in modality_pref else "Retinal Fundus"
    if modality == "None":
        ocular = False
    grad = _pick(d.get("gradability"), ["Adequate", "Borderline", "Ungradable"], "Adequate")
    if grad == "Ungradable":  # never reject a real eye photo for quality; flag it instead
        grad = "Borderline"
    return {"is_ocular": ocular, "modality": modality if ocular else "None", "gradability": grad,
            "image_type": "photograph", "reason": str(d.get("reason") or "")}, meta


# ---------------- Nodes 2-4: one consolidated structured call ----------------
async def analyze(keys, image, modality, patient):
    fundus = modality == "Retinal Fundus"
    markers = FUNDUS_MARKERS if fundus else ANTERIOR_MARKERS
    if fundus:
        grade = ('"grading":{"stage":0-4 (ICDR: 0 none,1 mild NPDR,2 moderate,3 severe,4 proliferative; laser scars/neovascularization => 4),'
                 '"macular_edema":"Absent"|"Suspected","confidence":0-100,"primary_finding":"short","rationale":"1 sentence"}')
    else:
        grade = ('"grading":{"severity":"Normal"|"Mild"|"Moderate"|"Urgent" (mature cataract/dense corneal opacity => Urgent),'
                 '"macular_edema":"Absent","confidence":0-100,"primary_finding":"short","rationale":"1 sentence"}')
    prompt = f"""Ophthalmic triage, modality: {modality}. Inspect the image for EXACTLY these markers: {markers}.
Cup-to-disc marker (fundus only): present=true only if ratio > 0.6. Report only what is visible. Patient context (must not bias visual findings): {_ctx_text(patient)}.
Pick ONE urgency tier exactly from: {TIERS}. Escalate for HbA1c >= 9, long diabetes duration, sight-threatening symptoms.
Be concise. Return ONE JSON object:
{{"markers":[{{"name":<marker name>,"present":true|false,"severity":"None"|"Mild"|"Moderate"|"Severe","quadrants":"short","detail":"max 10 words"}}],
"cup_to_disc_ratio":number|null,"summary":"1 sentence",
{grade},
"triage":{{"urgency_tier":<exact tier>,"clinical_actions":["3 short items"],"systemic_actions":["2 short items"],"follow_up":"interval","rationale":"1 sentence"}}}}"""
    return await call_model(keys, SYSTEM, prompt, image, timeout=20.0, max_tokens=1000)


def parse_bio(d, modality):
    markers = FUNDUS_MARKERS if modality == "Retinal Fundus" else ANTERIOR_MARKERS
    got = {str(m.get("name", "")).strip().lower(): m for m in (d.get("markers") or []) if isinstance(m, dict)}
    items = []
    for name in markers:
        m = got.get(name.lower())
        if m is None:
            m = next((v for k, v in got.items() if k[:8] == name.lower()[:8]), {})
        present = _bool(m.get("present"))
        sev = _pick(m.get("severity"), SEVERITY_LEVELS, "Mild" if present else "None")
        if not present:
            sev = "None"
        elif sev == "None":
            sev = "Mild"
        items.append({"name": name, "present": present, "severity": sev,
                      "quadrants": str(m.get("quadrants") or "n/a"), "detail": str(m.get("detail") or "")})
    try:
        cdr = round(float(d.get("cup_to_disc_ratio")), 2)
    except (TypeError, ValueError):
        cdr = None
    return {"items": items, "cup_to_disc_ratio": cdr, "summary": str(d.get("summary") or "")}


def parse_grade(d, modality):
    d = d.get("grading") if isinstance(d.get("grading"), dict) else {}
    conf = _conf(d.get("confidence"))
    edema = _pick(d.get("macular_edema"), ["Absent", "Suspected"], "Absent")
    if modality == "Retinal Fundus":
        stage = _clamp_int(d.get("stage"), 0, 4, 0)
        risk = "Low" if stage == 0 else ("Moderate" if stage <= 2 else "Severe")
        if edema == "Suspected" and risk == "Low":
            risk = "Moderate"
        g = {"kind": "fundus", "stage": stage, "headline": ICDR[stage], "severity": None}
    else:
        sev = _pick(d.get("severity"), ANTERIOR_SEV, "Mild")
        risk = {"Normal": "Low", "Mild": "Moderate", "Moderate": "Moderate", "Urgent": "Severe"}[sev]
        edema = "Absent"
        g = {"kind": "anterior", "stage": ANTERIOR_SEV.index(sev), "headline": f"Anterior Severity: {sev}", "severity": sev}
    g.update({"risk_level": risk, "macular_edema": edema, "confidence": conf,
              "primary_finding": str(d.get("primary_finding") or ""), "rationale": str(d.get("rationale") or "")})
    return g


def parse_triage(d, g):
    d = d.get("triage") if isinstance(d.get("triage"), dict) else {}
    picked = _pick(d.get("urgency_tier"), TIERS, None)
    idx = TIERS.index(picked) if picked else 0
    floor = _floor_tier(g)
    escalated = floor > idx
    idx = max(idx, floor)

    def lst(v):
        return [str(x) for x in v][:6] if isinstance(v, list) else []

    return {"urgency_tier": TIERS[idx], "urgency_index": idx, "escalated_by_rules": escalated,
            "clinical_actions": lst(d.get("clinical_actions")), "systemic_actions": lst(d.get("systemic_actions")),
            "follow_up": str(d.get("follow_up") or ""), "rationale": str(d.get("rationale") or "")}


def _floor_tier(g) -> int:
    s = g["stage"] or 0
    if g["kind"] == "fundus":
        floor = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3}[s]
        if g["macular_edema"] == "Suspected":
            floor = max(floor, 2)
    else:
        floor = {0: 0, 1: 0, 2: 1, 3: 3}[s]
    return floor


# ---------------- Node 5 ----------------
def compile_report(patient, gate, bio, g, tri, timings, providers):
    created = time.time()
    when = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M")
    present = [i for i in bio["items"] if i["present"]]
    findings_txt = ", ".join(f"{i['name']} ({i['severity'].lower()}, {i['quadrants']})" for i in present) or "no abnormal markers detected"
    summary = (f"{gate['modality']} scan ({gate['gradability']} quality). {g['headline']}"
               f"{'; macular edema suspected' if g['macular_edema'] == 'Suspected' else ''}. "
               f"Findings: {findings_txt}. Triage: {tri['urgency_tier']}.")
    pl = patient
    patient_md = "\n".join([
        f"- **Patient:** {pl.get('label') or 'Unlabeled'}",
        f"- **Age:** {pl.get('age') or 'n/a'}  |  **Diabetes duration:** {pl.get('diabetes_years') or 'n/a'} yrs  |  **HbA1c:** {pl.get('hba1c') or 'n/a'}%",
        f"- **Reported symptoms:** {pl.get('symptoms') or 'None reported'}",
    ])
    mk_lines = []
    for i in bio["items"]:
        if i["present"]:
            line = f"- ⚠ **{i['name']}** — PRESENT, {i['severity']} · {i['quadrants']}"
            if i["detail"]:
                line += f" · {i['detail']}"
        else:
            line = f"- ✓ **{i['name']}** — not detected"
        mk_lines.append(line)
    mk = "\n".join(mk_lines)

    def bullets(xs):
        return "\n".join(f"- {x}" for x in xs) or "- None specified"

    notable = "\n".join(f"- {i['name']}: {i['severity']} ({i['quadrants']})" for i in present) or "- No abnormal biomarkers detected"
    oct_txt = " and OCT for macular assessment" if g["macular_edema"] == "Suspected" else ""
    report_md = f"""# RetinaGuard Ocular Triage Report

*Generated {when}*

> {DISCLAIMER}

## Patient
{patient_md}

## Screening Result
- **Modality:** {gate['modality']} ({gate['gradability']} gradability)
- **Grade:** {g['headline']}
- **Overall risk:** {g['risk_level']}
- **Macular edema:** {g['macular_edema']}
- **Model confidence:** {g['confidence']}%
- **Primary finding:** {g['primary_finding'] or 'n/a'}

## Referral Urgency
**{tri['urgency_tier']}**

{tri['rationale']}

## Anatomical Biomarkers
{mk}

## Recommended Clinical Next Steps
{bullets(tri['clinical_actions'])}

## Systemic Care Actions
{bullets(tri['systemic_actions'])}

**Follow-up:** {tri['follow_up'] or 'per specialist'}

---
*{DISCLAIMER}*
"""
    letter = f"""# Ophthalmology Referral Note

**Date:** {when}
**Re:** {pl.get('label') or 'Unlabeled patient'} (Age {pl.get('age') or 'n/a'})
**Urgency:** {tri['urgency_tier']}

Dear Colleague,

I am referring the above patient following an AI-assisted ocular screening ({gate['modality']}, {gate['gradability'].lower()} image quality).

## Clinical Context
- Diabetes duration: {pl.get('diabetes_years') or 'n/a'} years; HbA1c: {pl.get('hba1c') or 'n/a'}%
- Reported symptoms: {pl.get('symptoms') or 'None reported'}

## Screening Impression
**{g['headline']}** — overall risk **{g['risk_level']}**; macular edema **{g['macular_edema']}**; model confidence {g['confidence']}%.
{g['rationale']}

## Notable Findings
{notable}

## Request
Please evaluate with dilated fundus / slit-lamp examination{oct_txt} within the stated timeframe.
Suggested actions: {'; '.join(tri['clinical_actions'][:3]) or 'per your assessment'}.

Sincerely,
_________________________
(Referring clinician)

*{DISCLAIMER}*
"""
    return {
        "created_at": created, "patient": patient, "gatekeeper": gate, "biomarkers": bio, "grading": g,
        "triage": tri, "summary": summary, "disclaimer": DISCLAIMER, "timings": timings, "providers": providers,
        "report_md": report_md, "referral_letter_md": letter,
    }



# ---------------- Orchestrator ----------------
def _ev(node, status, **kw):
    return {"event": "node", "node": node, "status": status, **kw}


async def run_pipeline(keys, image: bytes, thumb: str, patient: dict, modality_pref: str):
    """Yields NDJSON-ready events: start -> node(1..5) -> result. Two AI calls total."""
    T0 = time.perf_counter()
    timings, providers = {}, {}
    ms = lambda t: int((time.perf_counter() - t) * 1000)  # noqa: E731
    try:
        yield {"event": "start", "nodes": 5}
        yield _ev(1, "running")
        t = time.perf_counter()
        gate, meta = await node1(keys, image, modality_pref)
        timings["node1"] = ms(t)
        providers["node1"] = f"{meta['provider']} · {meta['model']}"
        if not gate["is_ocular"]:
            msg = "Image rejected: this does not appear to be an eye or retinal photograph. " + gate["reason"]
            yield _ev(1, "failed", data=gate, latency_ms=timings["node1"], message=msg)
            yield {"event": "rejected", "message": msg + " (This did not use a screening slot.)"}
            return
        if not db.consume_slot():
            yield {"event": "error", "message": "Hourly demo screening limit reached while the pipeline was running."}
            return
        yield _ev(1, "done", data=gate, latency_ms=timings["node1"], provider=providers["node1"])
        modality = gate["modality"]

        yield _ev(2, "running")
        t = time.perf_counter()
        d, meta = await analyze(keys, image, modality, patient)
        timings["node2_4"] = ms(t)
        prov = f"{meta['provider']} · {meta['model']}"
        providers.update(node2=prov, node3=prov, node4=prov)
        bio = parse_bio(d, modality)
        g = parse_grade(d, modality)
        tri = parse_triage(d, g)
        yield _ev(2, "done", data=bio, latency_ms=timings["node2_4"], provider=prov)
        yield _ev(3, "running")
        await asyncio.sleep(0.12)
        yield _ev(3, "done", data=g, provider=prov)
        yield _ev(4, "running")
        await asyncio.sleep(0.12)
        yield _ev(4, "done", data=tri, provider=prov)

        yield _ev(5, "running")
        t5 = time.perf_counter()
        timings["node5"] = 0
        timings["total"] = ms(T0)
        report = compile_report(patient, gate, bio, g, tri, timings, providers)
        sid = db.save_screening(report, thumb)
        timings["node5"] = ms(t5)
        timings["total"] = ms(T0)
        yield _ev(5, "done", data={"id": sid}, latency_ms=timings["node5"], provider="Local")
        report["thumb"] = thumb
        report["id"] = sid
        yield {"event": "result", "report": report}
    except LLMError as e:
        yield {"event": "error", "message": str(e)}
    except Exception as e:  # never let the stream die silently
        yield {"event": "error", "message": f"Pipeline error: {type(e).__name__}: {e}"}
