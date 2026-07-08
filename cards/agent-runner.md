# agent-runner

How the agent executes one workflow with the Claude Agent SDK. `agent/runner.py` + `agent/worker.py`.

**Worker loop** (`worker.py`): poll `claim_next` → if a job, make `sandboxes/<run_id>/`, report the
sandbox path, run the workflow, report `done` + result (or catch any exception and report `error`).
One job at a time; failures never kill the loop. Run more processes for more throughput.

**status.json** (`worker.handle`): the worker times each run with a monotonic clock and, in a
`finally` block, writes `status.json` into the run's sandbox — `{run_id, status, started_at,
finished_at, total_seconds}` (plus `error` on failure). `total_seconds` is the total wall-clock
running time. Written for both success and error paths; best-effort (`build_status` is pure/unit
tested, `write_status` swallows I/O errors so bookkeeping never fails a run). It lands in the sandbox
so it shows up in the file browser like any other deliverable.

**A workflow definition** carries: `inputs_spec`, `action_prompt` (what the agent does),
`eval_prompt` (how to summarize), `tools` (JSON list, e.g. `["puppeteer"]`), and `model`
(`''`/fable/opus/sonnet/haiku — `''` = the agent host's default).

**Transient API errors are retried** (`_run_query`): overload / rate-limit / timeout / connection
failures (`is_transient_error`, phrase-matched) get up to `RETRY_ATTEMPTS` (default 3) tries with
doubling backoff from `RETRY_BASE_SECONDS` (default 15s), each retry noted in the run log. A retry
restarts that pass from scratch — safe because the sandbox persists. Non-transient errors raise
immediately and fail the run.

**Running one workflow** (`run_workflow`):
1. Pull any uploaded files into the sandbox; write `inputs.json` there.
2. `tools_config(wf.tools)` → `(mcp_servers, allowed_tools)`. Base local tools
   (`Bash, Read, Write, Edit, Glob, Grep`) are always allowed. Each MCP tool adds an HTTP
   MCP server and an `mcp__<server>` entry to `allowed_tools`. A `tools` entry is either a
   registered tool name or an inline server dict `{"name", "type", "url", ...}` passed straight
   to the SDK (`name` becomes the server key; the rest is the config). Tool names are resolved
   **web-side at claim time** from the admin-managed `mcp_tools` table (Admin → Tools): enabled
   names arrive at the agent as inline dicts, disabled names are dropped with a run-log note, and
   unknown names pass through to the agent's `MCP_TOOLS` fallback dict in `runner.py`.
3. **Action pass:** `query()` with `cwd=sandbox`, `permission_mode="bypassPermissions"` (runs
   non-interactively), `max_turns`, `max_buffer_size=10 MB` (the SDK's 1 MB default kills the run
   when a single tool result — e.g. a fetched web page — exceeds it), and
   `model = wf.model or AGENT_MODEL env or SDK default`. The
   action prompt is augmented to tell the agent to write only inside the sandbox cwd (files outside
   are discarded) and to narrate progress via `log_message`. Collect the final
   `ResultMessage.result`; a result with `is_error` raises.
4. **Eval pass:** if `eval_prompt` is set, a second `query()` — read-only file tools plus the
   `log_message`/`next_workflow` tools — summarizes the action result and is told to Glob the sandbox
   and confirm any file it cites actually exists before reporting. Its output becomes the run's
   `result`. No eval prompt → return the action result (and the run can't chain).

**Chaining** (`run_workflow` is a loop, `_run_step` does one workflow's action+eval): the eval pass
gets two in-process tools — `workflow_inputs(name)` (read-only: returns a named workflow's
`inputs_spec` so the agent can see what a candidate next workflow expects) and
`next_workflow(name, message, inputs)` (routes, storing the name in `_CTX["next"]`, the optional
handover note in `_CTX["handover"]`, and an optional JSON object of values for the next workflow's
declared input keys in `_CTX["handover_inputs"]`). The eval prompt decides whether to chain and to
which workflow; the eval query carries a firm directive that chaining REQUIRES the `next_workflow`
tool call — a textual mention doesn't route (observed flake, run 28) — and not to call it otherwise. After a step, if a next workflow was chosen, the worker resolves it by name via
`web.resolve_workflow` (`GET /api/workflows/resolve`, scoped to the run's user's visibility), then
runs it **in the same sandbox** — so the next workflow sees every file produced so far. The filled
inputs are validated against the next workflow's `inputs_spec` (`filter_handover_inputs` — keys it
doesn't declare are dropped with a run-log note) and become the next step's `inputs` (surfaced under
`Inputs:` by `compose_prompt`); `inputs.json` in the sandbox is **rewritten per step** so the
filesystem agrees with the prompt. Its action prompt is staged with a chained-step preamble: the
name of the workflow it continues from, that step's result, the sandbox file listing (`list_files` —
recursive, capped at 100 entries, so nested deliverables show up) it's told to inspect first, the
handover note if one was passed (the tool asks for: what was produced + which files, what remains,
gotchas), and a note pointing at any handed-over inputs. Each
step emits a `▶ started`/`✓ finished workflow "X"` line to the run log (via `_emit`, the same
channel as `log_message`), so a chained flow shows a clear start/end event per workflow. The
whole chain is **one run row**; steps are narrated via `log_message`; the final step's output is the
run's `result`. Bounded by `MAX_CHAIN_STEPS` (default 10) to guard cycles/self-routing; an
unresolvable/ambiguous name ends the chain gracefully (logged, run still `done`). Later steps get no
new human inputs/files — they live off the shared sandbox. The chain runs entirely on the one host
that owns the sandbox (the loop never re-enters the queue, so a second worker can't claim a mid-chain
step).

**Progress log:** the action pass gets an in-process SDK tool `log_message` (`create_sdk_mcp_server`,
allowed as `mcp__progress__log_message`). When the agent calls it, the tool POSTs to the web run-log
API for the current run (run id held in a module global `_CTX` — safe since one worker runs one job
at a time). The action prompt nudges the agent to narrate steps; messages show live on the run page.

**Auth:** no API key. The SDK uses the Claude Code CLI's own login on the agent host. Never set
`ANTHROPIC_API_KEY` (it would override CLI auth). The host needs Node + the logged-in CLI.
