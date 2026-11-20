"""SQLite persistence: settings (API keys), rate-limit events, screening history."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "retinaguard.db"
WINDOW_SECONDS = 3600
_lock = threading.Lock()


def limit_per_hour() -> int:
    try:
        return max(1, int(os.getenv("RATE_LIMIT_PER_HOUR", "15")))
    except ValueError:
        return 15


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS rate_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS screenings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                label TEXT, modality TEXT, kind TEXT,
                stage INTEGER, stage_text TEXT, risk_level TEXT,
                urgency TEXT, confidence REAL,
                thumb TEXT, report_json TEXT NOT NULL
            );
            """
        )


# ---------- settings / keys ----------
def get_setting(key: str):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None


def set_setting(key: str, value: str):
    with _lock, _conn() as c:
        if value:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        else:
            c.execute("DELETE FROM settings WHERE key=?", (key,))


def resolve_key(provider: str):
    """Returns (key, source). DB-saved key wins over .env."""
    env_name = {"groq": "GROQ_API_KEY"}[provider]
    k = get_setting(f"{provider}_key")
    if k:
        return k, "app"
    k = os.getenv(env_name, "").strip()
    if k:
        return k, ".env"
    return None, None


# ---------- rate limiting ----------
def rate_status() -> dict:
    now = time.time()
    lim = limit_per_hour()
    with _conn() as c:
        rows = c.execute("SELECT ts FROM rate_events WHERE ts > ? ORDER BY ts ASC", (now - WINDOW_SECONDS,)).fetchall()
    used = len(rows)
    reset_in = int(max(0, rows[0]["ts"] + WINDOW_SECONDS - now)) + (1 if rows else 0) if rows else 0
    return {"limit": lim, "used": used, "remaining": max(0, lim - used), "reset_in": reset_in}


def consume_slot() -> bool:
    """Atomically consume one screening slot. False if the window is full."""
    now = time.time()
    with _lock, _conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM rate_events WHERE ts > ?", (now - WINDOW_SECONDS,)).fetchone()["n"]
        if n >= limit_per_hour():
            return False
        c.execute("INSERT INTO rate_events(ts) VALUES(?)", (now,))
        c.execute("DELETE FROM rate_events WHERE ts < ?", (now - 86400,))
        return True


# ---------- history ----------
def save_screening(report: dict, thumb: str) -> int:
    g = report["grading"]
    with _lock, _conn() as c:
        cur = c.execute(
            """INSERT INTO screenings(created_at,label,modality,kind,stage,stage_text,risk_level,urgency,confidence,thumb,report_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                report["created_at"], report["patient"].get("label") or "Unlabeled",
                report["gatekeeper"]["modality"], g["kind"], g.get("stage"), g["headline"],
                g["risk_level"], report["triage"]["urgency_tier"], g["confidence"], thumb, "{}",
            ),
        )
        sid = cur.lastrowid
        report["id"] = sid
        c.execute("UPDATE screenings SET report_json=? WHERE id=?", (json.dumps(report), sid))
        return sid


def list_screenings(limit=100):
    with _conn() as c:
        rows = c.execute(
            "SELECT id,created_at,label,modality,kind,stage,stage_text,risk_level,urgency,confidence,thumb FROM screenings ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_screening(sid: int):
    with _conn() as c:
        r = c.execute("SELECT report_json, thumb FROM screenings WHERE id=?", (sid,)).fetchone()
    if not r:
        return None
    rep = json.loads(r["report_json"])
    rep["thumb"] = r["thumb"]
    return rep


def delete_screening(sid: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM screenings WHERE id=?", (sid,))
