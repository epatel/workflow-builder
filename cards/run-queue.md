# run-queue

The run lifecycle and the agent-facing API contract. Web side: `web/app.py`. Agent side: `agent/client.py`.

**A run** is a row in `runs`: `status ∈ {pending, running, done, error}`, plus `inputs` (JSON),
`result`, `error`, `sandbox_path`. Created when a user submits a workflow's run form
(`POST /workflows/{id}/run`): text inputs go into `inputs` JSON; uploaded files are saved on the
web host under `uploads/<run_id>/` and referenced as `{"file": name}` in `inputs`.

**Workflow endpoint (external trigger API).** A workflow owner/admin can attach a single named
endpoint (table `endpoints`: globally unique `name` + bearer `token`; one per workflow, enforced in
`create_endpoint`). It's managed on the workflow page: when none exists the button reads
"Add endpoint" and opens the config dialog; once defined the button shows the endpoint name and
opens the same dialog to edit the token or delete it (token editable/regenerable at a click).
Next to the name button, an **ⓘ (info) icon-button** opens a separate read-only dialog with a
ready-to-run `curl` example (built client-side: `location.origin` + prefix + endpoint name, the
bearer token, and a JSON body pre-filled with the workflow's input keys) plus a Copy button.
`POST /api/endpoints/{name}` with that token
creates a pending run owned by the **workflow's owner**; inputs come as a JSON object (a string
for a `file` input is saved as `uploads/<run_id>/<key>.txt`) or as `multipart/form-data` with real
file parts for `file` inputs. Returns `{run_id, status, status_url}`.
`GET /api/endpoints/{name}/{rid}` polls `{status, result, error, data}` — `data` is the run's
sandbox file list (via the agent files proxy, empty if unreachable), each item downloadable at
`GET …/{name}/{rid}/data/{path}` (attachment semantics, so untrusted sandbox HTML never renders in
our origin). Both GETs 404 runs that don't belong to the endpoint's workflow; auth failures and
unknown endpoint names both return the same 401 so names can't be probed.

**Agent API (bearer-token auth).** The agent has no DB; it uses these endpoints. Auth is a single
shared secret: `Authorization: Bearer <AGENT_TOKEN>`, identical in `web/.env` and `agent/.env`.
`_check_agent` rejects anything else with 401.

- `POST /api/runs/next` — atomically claims the oldest `pending` run (`BEGIN IMMEDIATE`, flips it
  to `running`), returns `{run, workflow}`. Returns **204** when the queue is empty. The returned
  workflow's `tools` list is resolved against the admin-managed `mcp_tools` registry
  (`_resolve_tools`): enabled tool names become inline server dicts, disabled ones are dropped
  with a run-log note, unknown names pass through. Same resolution applies on
  `GET /api/workflows/resolve` (chaining).
- `POST /api/runs/{id}` — updates any of `status`, `result`, `error`, `sandbox_path`.
- `POST /api/runs/{id}/log` — appends a live progress message (`run_logs` table). The agent calls
  this via its `log_message` SDK tool while a workflow runs.
- `GET /api/runs/{id}/files/{name}` — streams an uploaded input file so the agent can pull it into
  its sandbox. `name` is basename-sanitized against path traversal.
- `POST /api/runs/reap-running` — the worker calls this on startup: with a single worker, any run
  still `running` was orphaned by a crashed/restarted worker, so it's marked `error` ("interrupted
  (agent restarted)") instead of being stuck forever. (Single-worker assumption — `claim_next` only
  picks `pending`, so an interrupted run is never re-claimed on its own.)
- `GET /api/workflows/resolve?name=&run_id=` — resolves the next workflow in a **chain** by name,
  scoped to the run's user's visibility (owner / admin / shares a set). `404` if no accessible match,
  `409` if the name is ambiguous. See **cards/agent-runner.md** for the chaining loop.

**Listing runs:** `GET /runs` (`list_runs`) shows a run history table — regular users see **only
their own** runs (`WHERE r.user_id=?`), admins see everyone's (with a "By" column). Linked from the
nav. The admin dashboard (`/admin`) still has its own all-runs table with delete/purge controls.
Per-workflow pages (`view_workflow`) list that workflow's own-user runs. Single-run access
(`view_run`, `/runs/{id}/status`, files) is gated by `_owned_run` (own-or-admin → else 404).

**Status for the browser:** `GET /runs/{id}/status` returns `{status, result, error, logs}` (logs =
the agent's progress messages); the run page polls it every 2s until `done`/`error`, rendering the
log live and (on completion) the `result` as markdown in a sandboxed iframe (`view_run` passes
`result_html`). (ponytail: polling, single worker, claim-by-update — add SSE / multiple workers only
if it feels slow.)

The claim is serialized by SQLite's write lock, so multiple agent workers won't double-claim. A
**chained** run stays one row spanning multiple workflows: the worker that claimed it runs the whole
chain in its sandbox without re-queueing, so it never moves between hosts mid-chain.
