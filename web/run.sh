#!/bin/sh
# Production run for the web server (behind Apache on rpi6).
# Apache strips /workflow, so --root-path tells the app its public prefix.
set -e
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
./venv/bin/python db.py   # apply pending migrations
exec ./venv/bin/uvicorn app:app \
  --host 127.0.0.1 --port "${PORT:-9005}" \
  --root-path "${ROOT_PATH:-/workflow}"
