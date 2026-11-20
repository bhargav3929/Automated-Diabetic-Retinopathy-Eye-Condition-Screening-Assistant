# RetinaGuard - Viva Notes

**Problem:** Diabetic retinopathy and other eye diseases are found late because specialists are few. RetinaGuard gives a fast first screening and a triage suggestion from one photo.

**Pipeline (app/pipeline.py):**
1. Ocular gatekeeper - rejects non-eye images, detects fundus vs anterior eye (rejections are free).
2. Biomarker detector - microaneurysms, haemorrhages, exudates, neovascularisation, cup-to-disc ratio, etc.
3. ICDR grader - stage 0-4 for fundus; Normal/Mild/Moderate/Urgent for anterior; macular-edema risk.
4. Triage - urgency tier with a rule-based floor so the AI can never under-triage a high grade.
5. Report - saved to SQLite, with a referral letter and progression comparison.

**AI:** Groq only. `qwen/qwen3.8-27b` (vision). Calls have timeouts, retry on 429/5xx with exponential backoff, fall back to the backup model, and show plain messages. Failed runs never count against the limit.

**Keys:** saved key in the app first, then `.env`. Key changes only accepted from the same machine (no proxy header).

**Limits:** 15 screenings per sliding hour (SQLite).

**Demo vs download:** same code; `DEMO_MODE=true` hides the key screen, requires a signed ticket for live runs, applies a daily cap and keeps each visitor's history private for 24 hours.

**Port:** 8002 (8000 + project number 2).

**Likely questions:**
- *Why not a trained CNN?* No GPU or large downloads for students; a vision LLM plus safety rules runs anywhere.
- *How do you prevent unsafe output?* Triage floor per grade and a clear "not a diagnosis" disclaimer.
- *Why SQLite?* Embedded, no server to install.
