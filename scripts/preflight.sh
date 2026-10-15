#!/usr/bin/env bash
# One command before you hit record: clean restart -> unit tests -> scripted demo walkthrough in a real browser.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv312/bin/python
pkill -f "uvicorn main:app" 2>/dev/null || true; sleep 1
echo "== tests =="; $PY -W ignore -m pytest -q tests
echo "== server =="; nohup .venv312/bin/uvicorn main:app --port 8000 >export/server.log 2>&1 &
for i in $(seq 1 20); do curl -sf localhost:8000/api/status >/dev/null && break; sleep 0.5; done
echo "== demo walkthrough =="; $PY -W ignore scripts/demo_check.py
echo "== viewer controls =="; $PY -W ignore scripts/controls_check.py
echo; echo "READY TO RECORD  ->  http://localhost:8000/?demo=1&theme=dark   (→ next, ← back, h hides panel)"
