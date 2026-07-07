# Project Plan — Workflow Builder

Read this first. It holds the shared goal, settled decisions, and current state. If you are a
subagent, read it before starting and update **Decisions** and **Current state** before finishing.

## Goal

Ship a browser tool where users define agent workflows and run them: pick a workflow, supply
inputs/files, start it, watch progress, read the result. Two FastAPI services — a web front end
(accounts + workflow CRUD + run queue) and an agent worker that runs each workflow with the
Claude Agent SDK in a per-run sandbox.

## Non-goals

- No refactor into a node/pipeline ("feature-first") architecture — two plain services is the model.
- No hosted/cloud Agents API, no public tunnel for the puppeteer MCP.
- No ORM / migration framework — stdlib SQLite + numbered migrations only.
- No speculative scaling (multi-worker queue infra, Postgres, SSE) until something measurably hurts.

## Milestones

- [x] Web service — accounts (signup/login/verify/reset/invites/roles), workflow CRUD, run queue, agent API. *Verified: `web/test_smoke.py`.*
- [x] Agent service — worker loop, SDK runner, sandbox, puppeteer MCP wiring, file pull. *Verified: `agent/test_agent.py`.*
- [x] README.
- [x] AI-doc setup — context cards + CLAUDE.md index + this plan.
- [x] Apache deploy config for `rpi6` (`/workflow`, port 9005) + `make deploy-web`/`deploy-agent`. Agent host needs no Apache (worker has no inbound server).
- [x] Deployed live and verified end-to-end: web in `screen workflow` on rpi6, agent in `screen workflow-agent` on home; a real workflow ran through the public URL via the Agent SDK (CLI auth) and returned its result.

## Decisions (append-only)

- 2026-06-21 — Stack mirrors an earlier internal project: FastAPI + JWT + SQLite + `mailjet-rest`, fronted by Apache. **Locked.**
- 2026-06-21 — Web owns the DB; agent has no DB and talks to the web JSON API with a shared bearer token. **Locked.**
- 2026-06-21 — Server-rendered Jinja2 UI (no build step); run progress via polling. **Locked.**
- 2026-06-21 — SQLite + numbered stdlib migrations (`PRAGMA user_version`), no ORM/Alembic. **Locked.** See cards/decision-sqlite-migrations.md.
- 2026-06-21 — Local Claude Agent SDK; puppeteer MCP stays on the LAN, no tunnel. **Locked.** See cards/decision-puppeteer-on-lan.md.
- 2026-06-21 — Agent auth via Claude Code CLI login, never `ANTHROPIC_API_KEY`. **Locked.** See cards/decision-cli-auth.md.
- 2026-06-21 — Web hosted under `/workflow` on rpi6:9005. Apache *strips* the prefix, app runs `--root-path /workflow`; templates use `<base href>` + relative links, redirects/cookie prefixed via root_path. **Locked.**
- 2026-06-21 — Bug found & fixed by new tests: `start_run` checked `isinstance(f, fastapi.UploadFile)`,
  but `request.form()` yields **starlette**'s `UploadFile` (the parent class), so every file upload was
  silently dropped. Fixed by importing `UploadFile` from `starlette.datastructures` in `web/app.py`.
- 2026-07-07 — `make test` fixed on Python 3.14: bumped `pydantic` `2.10.4`→`2.13.4` (old pin's
  `pydantic-core` had no cp314 wheel and failed to compile). Also fixed `agent/test_agent.py::test_chain_loop`,
  which contradicted `test_runner.py::test_filter_handover_inputs`: it expected an *undeclared* handover
  key to survive the drop-undeclared filter. Made the routed workflows declare the `to` input so the
  handover threading is genuine; corrected the `filter_handover_inputs` docstring (empty spec drops all,
  only an unparseable spec keeps everything).
- 2026-07-07 — Each run's total wall-clock running time is captured in a `status.json` at the root
  of the run's sandbox (`worker.handle` times the run with a monotonic clock; `build_status` builds
  the payload `{run_id, status, started_at, finished_at, total_seconds[, error]}`). Written in a
  `finally` for both done/error paths, best-effort. It's a sandbox file, so it surfaces in the file
  browser. Covered by `agent/test_agent.py` (`test_build_status` + status.json assertions in
  `test_handle_success`). See cards/agent-runner.md.
- 2026-06-21 — Sandbox file browsing is a **live passthrough**, not a copy-back: agent serves a read-only `/sandbox` file API in-process (`home:9006`, Apache `/workflow-agent`, token-auth, traversal-guarded); web proxies after authorizing the run owner. This adds an inbound server on the agent host (supersedes the earlier "agent needs no Apache" note). **Locked.** See cards/run-files.md.

## Current state / handoff

**Live.** Web on rpi6 (`screen workflow`, uvicorn :9005 behind Apache `/workflow`); agent worker on
home (`screen workflow-agent`). A real workflow ran end-to-end through `https://rpi6.memention.net/workflow`
and returned its SDK-generated result.

UI: a "Runs" nav link now opens `GET /runs` (`list_runs` + `runs.html`) — a run-history table
scoped to the viewer's own runs, except admins who see everyone's (with a "By" column). Covered by
`web/test_smoke.py` (own-vs-admin visibility). See cards/run-queue.md.

UI: an **Account** page (`GET/POST /account`, linked from the nav name) lets any user set a
**default model** (`users.default_model`, migration 005). This makes the workflow picker's
long-standing "Account default" option real: a workflow with `model=''` now resolves to the
run-starter's account default at claim time (`claim_next_run`), falling back to the host default
when that's also empty. Covered by `web/test_smoke.py`. See cards/accounts-auth.md.

UI: API endpoints are now **one per workflow**. The workflow page shows a single button: it reads
"Add endpoint" when none is defined and shows the endpoint's name once it is; either way it opens
the same config `<dialog>` to add, edit the token, or delete. `create_endpoint` enforces the
one-per-workflow limit server-side (bounces with an `ep_error` if a second is attempted). Covered by
`web/test_smoke.py::test_endpoints`. See cards/run-queue.md. (`endpoints` table is unchanged; the
global-unique `name` still holds.)

UI: next to the endpoint-name button an **ⓘ icon-button** opens a read-only dialog showing a
ready-to-run `curl` example — the full POST URL (`location.origin` + prefix + endpoint name, built
client-side), the bearer token, and a JSON body pre-filled from the workflow's input keys — with a
Copy button. Only rendered when an endpoint exists (`workflow_view.html`). See cards/run-queue.md.

UI: the "New workflow" `+` next to the Workflows heading is now a proper circular icon button —
reusable `.icon-btn` class in `base.html` (equal 1.9rem dims, flex-centered glyph, hover/active
feedback), replacing the old asymmetric-padding oval. The workflow-view "show curl" info (ⓘ)
button uses `.icon-btn secondary`; a `.icon-btn.secondary` rule now drops the redundant outer
border (the glyph is already a circled-i) and `.icon-btn` gets `vertical-align:middle` so it lines
up with the taller adjacent endpoint-name button.

Runs now leave a `status.json` in their sandbox with the **total running time** (`total_seconds`)
plus status and start/finish timestamps — see Decisions (2026-07-07) and cards/agent-runner.md.

**Deploy gotcha (seen 2026-06-21):** `make deploy-web` only rsyncs files — it does NOT restart the
uvicorn process. A "not found" / 404 on a route that exists in the deployed `app.py` (e.g. `/runs`)
almost always means the `screen workflow` process predates the deploy and is running stale code in
memory. Diagnose by comparing the process start time to `app.py` mtime; fix by restarting the screen
(below). Quick signal: a *deployed* protected route returns 401/303 when logged out — a 404 means the
route isn't in the running process.

  - **Recurrence (2026-06-21, later):** `/workflow/account` 404'd in prod. Confirmed exactly this:
    deployed `app.py` (mtime 20:03) HAD the route + template, but the uvicorn process had started
    19:53 — stale. Diagnosis signal held: `/runs`→401, `/account`→404. Fixed by restarting the
    screen; `/account` now returns 401. No code change — the source was already correct. This
    footgun keeps biting; consider folding a restart step into `make deploy-web`.

Restart web: `ssh rpi6 'screen -S workflow -X quit; sleep 2; cd ~/workflow-web && screen -dmS workflow bash -c "./run.sh > run.log 2>&1"'`.
Restart agent: `ssh home 'screen -dmS workflow-agent bash -c "cd ~/workflow-agent && ./run.sh > run.log 2>&1"'`.
Neither survives a host reboot (screen, per host convention) — relaunch manually if rebooted.

Gotcha: on `home` the npm-global `/usr/local/bin/claude` is a broken stub (native binary not
installed); the working CLI is `~/.local/bin/claude`. `agent/run.sh` prepends `~/.local/bin` to PATH
so the SDK finds it. Note: agent stdout is block-buffered to `run.log` — "polling" won't appear
until the buffer flushes; check the process (`pgrep -f worker.py`), not the log, to confirm it's up.

Tests: beyond the original smoke suites there are now two deeper suites wired into `make test`:
`web/test_logic.py` (set-authorization invariant in `_save_workflow_sets`, reset-token single-use/expiry,
end-to-end password reset, upload-filename traversal sanitization, age-based run purge) and
`agent/test_runner.py` (`_run_query` result-extraction: ResultMessage preferred, fallback to assistant
text, error result raises). The upload test caught a real bug — see Decisions (starlette vs fastapi
`UploadFile`). `make test` now builds and passes on Python 3.14: `pydantic` was bumped `2.10.4`→`2.13.4`
(`pydantic-core` 2.46.4 ships a cp314 wheel); the rest of the stack already had 3.14 wheels, so no
3.12-specific interpreter is required anymore.

Board shortcuts added for `make test` and `make deploy`. The Makefile now has an aggregate
`deploy` target (= `deploy-web` + `deploy-agent`) so the shortcut runs both services in one shot.

Next: wire Mailjet keys (currently unset → verification/reset links print to the agent... no, to the
web `run.log`), extend `MCP_TOOLS` for more agent tools (puppeteer is wired but untested in prod),
and decide reboot-survival (systemd vs screen) if uptime matters.

## Open questions

- Keeping the web worker alive on rpi6 (systemd unit vs screen/tmux) — pick the host's existing convention.
- Do we need rate-limiting (`slowapi`) and CORS before public exposure, or after?
