#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
"$PY" -m pip install -q -r requirements.txt
(sleep 3; command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8002 >/dev/null 2>&1 || true) &
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8002
