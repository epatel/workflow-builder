# architecture

How the two services fit together and deploy.

Two Python/FastAPI services that share no process and no database driver — they talk over HTTP.

- **web** (`web/`, host `rpi6`, public `/workflow`) — server-rendered Jinja2 UI plus a JSON API.
  Owns the only datastore: a SQLite file (`web/workflow.db`). Handles accounts, workflow
  definitions, and the run queue. Uvicorn on `:9005` behind Apache, which strips the `/workflow`
  prefix; the app runs `--root-path /workflow` so links/redirects/cookies stay correct under the
  sub-path (`deploy/rpi6/workflow.conf`).
- **agent** (`agent/`, host `home`) — a worker loop (no DB access) that polls the web JSON API for
  pending runs, executes each with the Claude Agent SDK in a per-run sandbox folder, and POSTs
  status/result back. Reaches a puppeteer MCP on the LAN. It also serves a small **read-only sandbox
  file API** (in-process, `:9006`, exposed via home's Apache at `/workflow-agent`) so the web host can
  browse/view/download a run's files live — the web server proxies to it (passthrough), nothing is
  copied back.

Data flow for one run: browser → web creates `runs(status=pending)` → agent `POST /api/runs/next`
claims it (→ running) → agent runs the workflow's prompts in `sandboxes/<run_id>/` →
agent `POST /api/runs/<id>` with the result (→ done/error) → browser polls `/runs/<id>/status`.

Why split across two hosts: the agent host is on the LAN with the puppeteer MCP and runs the
Claude Code CLI; the web host is the public front door. Keeping the DB on web means the agent
crossing hosts never needs shared filesystem or DB credentials — just a bearer token.

Run it locally: `make migrate`, `make web` (:8000), `make agent`, `make test`.
