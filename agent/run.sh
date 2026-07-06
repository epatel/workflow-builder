#!/bin/sh
# Run the agent worker on `home`. Uses Claude Code CLI auth (no ANTHROPIC_API_KEY).
set -e
cd "$(dirname "$0")"
# Prefer the user-local Claude Code CLI; the npm-global one may be a broken stub.
export PATH="$HOME/.local/bin:$PATH"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/python worker.py
