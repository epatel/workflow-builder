# Workflow Builder

A browser tool for defining agent workflows and running them: a user picks a workflow, supplies
inputs/files, starts it, and watches the result. Two FastAPI services — a **web** front end
(accounts, workflow CRUD, the run queue, SQLite) and an **agent** worker that executes each run
with the Claude Agent SDK in its own sandbox folder. Python 3.12+; the agent host also needs Node
and a logged-in Claude Code CLI.

Read the project plan first — it holds the shared goal, settled decisions, and current state:
@project-plan.md

## Commands

```sh
make venv          # web venv (.venv) + deps;   make agent-venv for agent/.venv
make web           # web on :8000 with --reload (reads web/.env)
make agent         # agent worker (reads agent/.env; needs Claude Code CLI logged in)
make migrate       # apply pending web/migrations/NNN_*.sql (also runs on web startup)
make test          # all tests: web test_smoke + test_logic, agent test_agent + test_runner
cd web && ../.venv/bin/python test_smoke.py           # one test file (plain-assert scripts, no pytest)
cd agent && ./.venv/bin/python test_runner.py
```

No linter or build step.

## Terminology

What the code and UI call a **workflow** is conceptually a **task**: one node (action prompt +
eval prompt + tools) that a run executes, possibly chaining to the next node. "Workflow" is the
better name for the whole chain. Nothing is renamed yet — code, DB, and URLs still say
`workflow(s)` — but when writing prose/docs or naming new things, think of a workflow row as a
task-node, and don't overload "workflow" further.

## UI conventions

Never use browser-default popups — no `alert()`, `confirm()`, `prompt()`. Use a styled `<dialog>`
(see `workflow_view.html`); for confirmations, put `data-confirm="message"` on the form — the app
confirm dialog in `base.html` intercepts the submit. A PreToolUse hook
(`.claude/hooks/no-browser-popups.py`) blocks edits that add these calls under `web/templates|static/`.

## Deploy

Live at https://rpi6.memention.net/workflow. Both services run as detached `screen` sessions
(no systemd; they don't survive a host reboot). See the README **Deploy** section for the full
runbook; the short version:

```sh
make deploy-web      # rsync web/   -> rpi6:~/workflow-web  + install/reload Apache (strips /workflow -> :9005)
make deploy-agent    # rsync agent/ -> home:~/workflow-agent (worker + file API on :9006 via Apache /workflow-agent)
# then restart the affected screen, e.g. on rpi6:
ssh rpi6 'screen -S workflow -X quit; sleep 2; cd ~/workflow-web && screen -dmS workflow bash -c "./run.sh > run.log 2>&1"'
# agent screen is named "workflow-agent"; web migrations apply automatically on restart via run.sh.
```

`.env` files live on the hosts and are never synced (deploy excludes `.env`/`*.db`/`venv`/sandboxes).
`home` needs the Claude Code CLI logged in (no `ANTHROPIC_API_KEY`).

## Context cards

Load a card when the situation matches its trigger.

- [architecture](cards/architecture.md) — anything about how the two services connect, the request/run flow end to end, or where things are deployed.
- [accounts-auth](cards/accounts-auth.md) — touching signup, login, sessions, roles/permissions, invite codes, email verification, password reset, or **sets / workflow visibility / who-can-see-a-workflow**.
- [run-queue](cards/run-queue.md) — touching how runs are created, claimed, or reported; the agent-facing `/api/runs/*` endpoints; or run status polling.
- [run-files](cards/run-files.md) — touching browsing/viewing/downloading a run's sandbox files, the agent's `/sandbox/*` file API, or the web→agent passthrough.
- [agent-runner](cards/agent-runner.md) — touching how the agent executes a workflow: the worker loop, sandboxes, tool/MCP wiring, or the action/eval passes.
- [decision-cli-auth](cards/decision-cli-auth.md) — when anything involves the agent's API key or authentication, or `ANTHROPIC_API_KEY` comes up.
- [decision-sqlite-migrations](cards/decision-sqlite-migrations.md) — when changing the database schema or wondering how migrations work here.
- [decision-puppeteer-on-lan](cards/decision-puppeteer-on-lan.md) — when wiring browser/MCP tools, considering the hosted Agents API, or thinking about exposing puppeteer.

When a change adds non-obvious logic, settles an architectural choice, or makes a card above wrong,
update or add the card in the same change and keep this index in sync — and the README too, so
neither drifts from the code. Cards stay self-contained: no card links to another.
