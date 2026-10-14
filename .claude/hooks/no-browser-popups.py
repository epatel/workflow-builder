#!/usr/bin/env python3
"""PreToolUse hook: block alert()/confirm()/prompt() in web UI files.

The app uses styled <dialog>s instead of browser-default popups (CLAUDE.md, UI conventions).
Exit 2 blocks the edit and feeds stderr back to Claude.
"""
import json
import re
import sys

POPUP = re.compile(r"(?<![\w.$-])(?:window\.)?(alert|confirm|prompt)\([^)]")
UI_FILE = re.compile(r"(^|/)web/(templates|static)/.*\.(html|js)$")

data = json.load(sys.stdin)
inp = data.get("tool_input", {})
if not UI_FILE.search(inp.get("file_path", "")):
    sys.exit(0)

texts = [inp.get("content", ""), inp.get("new_string", "")]
texts += [e.get("new_string", "") for e in inp.get("edits", [])]
hit = next((m for t in texts for m in [POPUP.search(t)] if m), None)
if hit:
    print(f"Blocked: `{hit.group(1)}(` is a browser-default popup. Use a styled <dialog> "
          "(see workflow_view.html); for confirmations put data-confirm=\"message\" on the form "
          "and the app confirm dialog in base.html intercepts the submit.", file=sys.stderr)
    sys.exit(2)
