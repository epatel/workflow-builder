"""Run one workflow with the Claude Agent SDK inside a per-run sandbox folder."""
import asyncio
import json
import os
from pathlib import Path

from claude_agent_sdk import (query, ClaudeAgentOptions, AssistantMessage,
                              ResultMessage, TextBlock, tool, create_sdk_mcp_server)

PUPPETEER_MCP_URL = os.getenv("PUPPETEER_MCP_URL", "http://localhost:8765/mcp")
MODEL = os.getenv("AGENT_MODEL") or None

# Per-run context for the log tool. Safe as a module global: one worker, one job at a time.
_CTX: dict = {}


@tool("log_message",
      "Post a short progress update to the user watching this run live. "
      "Call it as you work to narrate steps, findings, and milestones.",
      {"text": str})
async def _log_message(args):
    if _CTX:
        try:
            await _CTX["web"].log(_CTX["run_id"], args["text"])
        except Exception:
            pass  # logging is best-effort; never fail the run over it
    return {"content": [{"type": "text", "text": "logged"}]}


@tool("workflow_inputs",
      "Look up another workflow's declared inputs (its inputs spec) by exact name, so you can "
      "adapt your handover before chaining. Returns the JSON inputs spec, or a not-found note.",
      {"name": str})
async def _workflow_inputs(args):
    name = (args.get("name") or "").strip()
    spec = f"(no accessible workflow named {name!r})"
    if _CTX:
        wf = await _CTX["web"].resolve_workflow(name, _CTX["run_id"])
        if wf is not None:
            spec = wf.get("inputs_spec") or "[]"
    return {"content": [{"type": "text", "text": spec}]}


@tool("next_workflow",
      "Continue this run by handing off to another workflow, which runs next in the SAME "
      "working directory (it sees every file you produced). Pass the exact workflow name, and "
      "a 'message': a handover note for the next workflow's agent covering (1) what was produced "
      "and which files hold it, (2) what remains to be done, (3) any gotchas or caveats the next "
      "agent should know. To fill the next workflow's declared inputs as part of the handover, "
      "pass 'inputs': a JSON object mapping its input keys to values, e.g. "
      "{\"language\": \"German\", \"tone\": \"formal\"} — call workflow_inputs(name) first to see "
      "which keys it expects; unknown keys are dropped. Those values are handed to the next "
      "workflow as its inputs. Call this only when the eval prompt says to chain; otherwise do not "
      "call it and the run ends.",
      {"name": str, "message": str, "inputs": str})
async def _next_workflow(args):
    _CTX["next"] = (args.get("name") or "").strip()
    _CTX["handover"] = (args.get("message") or "").strip()
    try:
        parsed = json.loads(args.get("inputs") or "{}")
        _CTX["handover_inputs"] = parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        _CTX["handover_inputs"] = {}  # ignore malformed inputs rather than fail the run
    return {"content": [{"type": "text", "text": f"will continue with: {_CTX['next']}"}]}


PROGRESS_SERVER = create_sdk_mcp_server(
    "progress", "1.0.0", tools=[_log_message, _workflow_inputs, _next_workflow])
PROGRESS_TOOL = "mcp__progress__log_message"
INPUTS_TOOL = "mcp__progress__workflow_inputs"
ROUTING_TOOL = "mcp__progress__next_workflow"

# A chain may visit at most this many workflows (guards cycles / self-routing).
MAX_CHAIN_STEPS = int(os.getenv("MAX_CHAIN_STEPS", "10"))

# Local file tools the agent always gets inside its sandbox.
BASE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]

# Workflow tool name -> (mcp server name, url). FALLBACK ONLY: the web app now
# resolves registered tool names to inline server dicts at claim time (admin-managed
# mcp_tools table, Admin → Tools). This dict only catches names the web didn't know.
MCP_TOOLS = {"puppeteer": ("puppeteer", PUPPETEER_MCP_URL)}


def tools_config(wf_tools: list) -> tuple[dict, list[str]]:
    """Map a workflow's tool list to (mcp_servers, allowed_tools). Pure — unit tested.

    Each entry is either a known shortcut name (e.g. "puppeteer") or an inline MCP
    server dict: {"name": ..., "type": "http"|"sse", "url": ...} (extra keys like
    "headers" pass straight through to the SDK).
    """
    mcp_servers, allowed = {}, list(BASE_TOOLS)
    for entry in wf_tools:
        if isinstance(entry, dict):
            server = entry["name"]
            mcp_servers[server] = {k: v for k, v in entry.items() if k != "name"}
        elif entry in MCP_TOOLS:
            server, url = MCP_TOOLS[entry]
            mcp_servers[server] = {"type": "http", "url": url}
        else:
            continue
        allowed.append(f"mcp__{server}")  # allow every tool from that server
    return mcp_servers, allowed


def list_files(sandbox: Path, cap: int = 100) -> list[str]:
    """Sandbox-relative paths of every file, recursively, capped. Pure-ish — unit tested.
    Nested deliverables (e.g. report/index.html) must show up in the handover listing."""
    paths = sorted(p.relative_to(sandbox).as_posix()
                   for p in sandbox.rglob("*") if p.is_file())
    if len(paths) > cap:
        paths = paths[:cap] + [f"… (+{len(paths) - cap} more)"]
    return paths


def filter_handover_inputs(inputs_spec: str, inputs: dict) -> tuple[dict, list[str]]:
    """Keep only handover inputs whose keys the next workflow declares. Pure — unit tested.
    Returns (kept, dropped_keys). An empty spec declares nothing, so every input is dropped;
    an unparseable spec has no contract to enforce, so everything is kept."""
    try:
        keys = {f["key"] for f in json.loads(inputs_spec or "[]") if isinstance(f, dict) and "key" in f}
    except (ValueError, TypeError):
        return inputs, []
    if not keys:
        return ({}, sorted(inputs)) if inputs else ({}, [])
    kept = {k: v for k, v in inputs.items() if k in keys}
    return kept, sorted(set(inputs) - keys)


def compose_prompt(action_prompt: str, inputs: dict, files: list[str]) -> str:
    """Build the action prompt from the workflow + this run's inputs. Pure — unit tested."""
    parts = [action_prompt, ""]
    text_inputs = {k: v for k, v in inputs.items() if not isinstance(v, dict)}
    if text_inputs:
        parts.append("Inputs:")
        parts += [f"- {k}: {v}" for k, v in text_inputs.items()]
    if files:
        parts.append("")
        parts.append("Files available in your working directory: " + ", ".join(files))
    return "\n".join(parts)


# Transient API failures worth retrying (matched against the error text, lowercased).
# Deliberately phrase-based — bare status-code numbers would false-match prompt content.
_TRANSIENT_ERRORS = ("overloaded", "rate limit", "rate_limit", "timeout", "timed out",
                     "connection", "temporarily unavailable", "service unavailable",
                     "internal server error")
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))       # total tries, incl. the first
RETRY_BASE_SECONDS = float(os.getenv("RETRY_BASE_SECONDS", "15"))  # 15s, 30s, ... doubling


def is_transient_error(msg: str) -> bool:
    """Should this SDK/API failure be retried? Pure — unit tested."""
    msg = (msg or "").lower()
    return any(p in msg for p in _TRANSIENT_ERRORS)


async def _run_query_once(prompt: str, options: ClaudeAgentOptions) -> str:
    """Run a query to completion, return the final result text."""
    final, texts = None, []
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            if msg.is_error:
                raise RuntimeError(msg.result or "agent run failed")
            final = msg.result
    return final or "\n".join(texts)


async def _run_query(prompt: str, options: ClaudeAgentOptions) -> str:
    """_run_query_once with backoff on transient API errors (overload, rate limit,
    network). A retry restarts the pass from scratch — safe: the sandbox persists and
    the prompt tells the agent to inspect existing files first."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return await _run_query_once(prompt, options)
        except Exception as e:
            if attempt == RETRY_ATTEMPTS or not is_transient_error(str(e)):
                raise
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            await _emit(f"transient API error ({e}) — retrying in {delay:.0f}s "
                        f"(attempt {attempt + 1}/{RETRY_ATTEMPTS})")
            await asyncio.sleep(delay)


async def _emit(msg: str):
    """Best-effort progress line to the run log (same channel as the log_message tool)."""
    if _CTX:
        try:
            await _CTX["web"].log(_CTX["run_id"], msg)
        except Exception:
            pass


async def _run_step(wf: dict, sandbox: Path, inputs: dict, files: list[str],
                    previous_result: str | None, previous_name: str | None,
                    handover: str | None) -> str:
    """Run one workflow's action + eval passes in the sandbox. Returns the eval summary.
    The eval pass may call next_workflow(...) to set _CTX["next"] (chaining)."""
    await _emit(f"▶ started workflow “{wf['name']}”")
    mcp_servers, allowed = tools_config(json.loads(wf.get("tools") or "[]"))
    # The agent can narrate progress to the run page via the log_message tool.
    mcp_servers = {**mcp_servers, "progress": PROGRESS_SERVER}
    allowed = allowed + [PROGRESS_TOOL]
    opts = dict(cwd=str(sandbox), permission_mode="bypassPermissions",
                allowed_tools=allowed, mcp_servers=mcp_servers,
                max_turns=int(os.getenv("MAX_TURNS", "40")),
                # A single large tool result (e.g. a fetched web page) can exceed the
                # SDK's 1 MB default message buffer and kill the run.
                max_buffer_size=10 * 1024 * 1024,
                model=(wf.get("model") or MODEL))   # per-workflow model; '' -> host default

    action_prompt = (wf["action_prompt"] +
                     "\n\nWrite every deliverable into the current working directory (the run "
                     "sandbox). Never write to /tmp or any absolute path outside the cwd — files "
                     "outside the sandbox are invisible to the user and get discarded."
                     "\n\nAs you work, call the log_message tool to post brief progress "
                     "updates (one short line each) so the user can follow along live.")
    if previous_result is not None:  # chained step: set the stage from the prior step
        src = f'the "{previous_name}" workflow' if previous_name else "a previous workflow"
        action_prompt += (
            f"\n\nThis is a chained step: you are continuing the work {src} already started, in the "
            "same working directory. Its files — the deliverables it produced plus inputs.json "
            "(this step's inputs) — are already here; inspect them before doing anything. "
            f"That previous step's result was:\n{previous_result}")
        if handover:
            action_prompt += (f"\n\nHandover note from that step (written for this workflow):\n"
                              f"{handover}")
        if inputs:  # values the previous step filled in for this workflow's declared inputs
            action_prompt += ("\n\nThe previous step also supplied values for this workflow's "
                              "declared inputs as part of the handover; they are listed under "
                              "'Inputs:' below — treat them as this run's inputs.")
    action_result = await _run_query(
        compose_prompt(action_prompt, inputs, files),
        ClaudeAgentOptions(**opts))

    eval_prompt = (wf.get("eval_prompt") or "").strip()
    if not eval_prompt:
        await _emit(f"✓ finished workflow “{wf['name']}”")
        return action_result

    # Eval pass: read-only summary; may inspect (workflow_inputs) and route (next_workflow).
    eval_opts = dict(opts,
                     allowed_tools=["Read", "Glob", "Grep", PROGRESS_TOOL, INPUTS_TOOL, ROUTING_TOOL],
                     mcp_servers={"progress": PROGRESS_SERVER})
    result = await _run_query(
        f"{eval_prompt}\n\nBefore reporting, list the sandbox (Glob '**/*') and confirm each "
        f"file you cite as a deliverable actually exists there. Do not claim a file exists "
        f"unless you saw it in the listing."
        f"\n\nIf the instructions above say to continue with, hand off to, or start another "
        f"workflow, you MUST call the next_workflow tool with that workflow's exact name — "
        f"mentioning the handoff in your summary does nothing; only the tool call chains. "
        f"If they don't say to chain, do not call next_workflow."
        f"\n\nResult to evaluate:\n{action_result}",
        ClaudeAgentOptions(**eval_opts))
    await _emit(f"✓ finished workflow “{wf['name']}”")
    return result


async def run_workflow(wf: dict, run: dict, sandbox: Path, web) -> str:
    """Run the workflow, then follow any eval-prompt chaining into further workflows that
    share this sandbox. Returns the final step's result. The whole chain is one run row."""
    _CTX.clear()
    _CTX.update(web=web, run_id=run["id"])  # the log/next tools read this
    inputs = json.loads(run.get("inputs") or "{}")

    # Pull any uploaded files into the sandbox (only the first step has human inputs).
    files = []
    for v in inputs.values():
        if isinstance(v, dict) and "file" in v:
            await web.download_file(run["id"], v["file"], sandbox / v["file"])
            files.append(v["file"])
    (sandbox / "inputs.json").write_text(json.dumps(inputs, indent=2))

    result, previous_result, previous_name, handover = None, None, None, None
    for step in range(1, MAX_CHAIN_STEPS + 1):
        _CTX.pop("next", None)       # cleared each step; the eval tools set these if it routes
        _CTX.pop("handover", None)
        _CTX.pop("handover_inputs", None)
        result = await _run_step(wf, sandbox, inputs, files, previous_result, previous_name, handover)

        nxt = _CTX.get("next")
        if not nxt:
            break
        if step == MAX_CHAIN_STEPS:
            await web.log(run["id"], f"chain step cap ({MAX_CHAIN_STEPS}) reached — stopping")
            break
        await web.log(run["id"], f"step done → routing to workflow “{nxt}”")
        nwf = await web.resolve_workflow(nxt, run["id"])
        if nwf is None:
            await web.log(run["id"], f"could not resolve workflow “{nxt}” — ending chain")
            break
        # Subsequent steps reuse the same sandbox. Hand forward the prior step's name + result +
        # optional handover note and the inputs it filled; list what's now in the sandbox.
        previous_name, previous_result, handover = wf.get("name"), result, _CTX.get("handover")
        wf = nwf
        # Only keys the next workflow declares survive the handover — drop the rest, visibly.
        inputs, dropped = filter_handover_inputs(nwf.get("inputs_spec") or "[]",
                                                 _CTX.get("handover_inputs") or {})
        if dropped:
            await web.log(run["id"],
                          f"handover inputs not declared by “{nxt}” dropped: {', '.join(dropped)}")
        # Keep the filesystem in sync with the prompt: inputs.json is this step's inputs.
        (sandbox / "inputs.json").write_text(json.dumps(inputs, indent=2))
        files = list_files(sandbox)
    return result
