"""Agent worker: poll the web queue, run each workflow, report back.

ponytail: one worker, one job at a time. Run more processes if throughput matters.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn

from client import WebClient
import runner
import files_api

WEB_URL = os.getenv("WEB_URL", "http://localhost:8000")
AGENT_TOKEN = os.environ["AGENT_TOKEN"]  # required; matches the web server's token
SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", Path(__file__).parent / "sandboxes"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
STOP_POLL_SECONDS = float(os.getenv("STOP_POLL_SECONDS", "3"))  # how often a live run checks for Stop
HTTP_PORT = int(os.getenv("AGENT_HTTP_PORT", "9006"))  # sandbox file API (behind Apache)


def build_status(run_id, status: str, started_at: datetime, finished_at: datetime,
                 elapsed_seconds: float, error: str | None = None,
                 files: list[str] | None = None) -> dict:
    """Assemble the status.json payload for a finished run. Pure — unit tested.
    total_seconds is the wall-clock running time (monotonic), rounded to milliseconds.
    files is the sandbox listing at finish time (excludes status.json itself — it is
    listed before the write)."""
    data = {
        "run_id": run_id,
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total_seconds": round(elapsed_seconds, 3),
    }
    if error is not None:
        data["error"] = error
    if files is not None:
        data["files"] = files
    return data


def write_status(sandbox: Path, data: dict):
    """Best-effort: drop status.json (with the run's total running time) in the sandbox."""
    try:
        (sandbox / "status.json").write_text(json.dumps(data, indent=2))
    except Exception as e:  # never let bookkeeping fail the run
        print(f"[run {data.get('run_id')}] could not write status.json: {e}")


async def watch_for_stop(web: WebClient, run_id, task: asyncio.Task,
                         interval: float | None = None) -> bool:
    """Cancel `task` once the web side no longer has this run as 'running' — the user
    pressed Stop (or the run was deleted). Returns True if it cancelled. A web hiccup
    is not a stop: on a failed poll the run keeps going."""
    interval = STOP_POLL_SECONDS if interval is None else interval
    while not task.done():
        await asyncio.sleep(interval)
        try:
            status = await web.run_status(run_id)
        except Exception:
            continue
        if status != "running" and not task.done():
            task.cancel()
            return True
    return False


def sandbox_bytes(sandbox: Path) -> int:
    """Total size of every regular file under the sandbox (apparent size, symlinks not
    followed). Reported as the run's disk usage so users can see what deleting it frees."""
    total = 0
    for dirpath, _dirs, names in os.walk(sandbox):
        for n in names:
            p = Path(dirpath) / n
            try:
                if not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass   # vanished mid-walk — ignore
    return total


def _stopped_outcome():
    # Stopped by the user: the web side already finished the run, so the final report
    # carries only bookkeeping (disk_bytes).
    return "stopped", "stopped by user", {}


async def _run_outcome(task: asyncio.Task, watcher: asyncio.Task, run_id):
    """Await the workflow task; return (status, error, final-report fields). Re-raises a
    CancelledError that isn't a user stop (the worker itself is shutting down)."""
    def user_stopped():
        return watcher.done() and watcher.result()
    try:
        result = await task
    except asyncio.CancelledError:
        if not user_stopped():
            raise
        print(f"[run {run_id}] stopped by user")
        return _stopped_outcome()
    except Exception as e:  # report failure, keep the worker alive
        if user_stopped():   # the cancel surfaced as an SDK error
            print(f"[run {run_id}] stopped by user ({e})")
            return _stopped_outcome()
        print(f"[run {run_id}] error: {e}")
        return "error", str(e), {"status": "error", "error": str(e)}
    print(f"[run {run_id}] done")
    return "done", None, {"status": "done", "result": result}


def _safe_list_files(sandbox: Path):
    try:
        return runner.list_files(sandbox)
    except Exception:  # listing is bookkeeping too — never fail the run over it
        return None


async def _final_report(web: WebClient, run_id, sandbox: Path, outcome: dict):
    """One final report: the outcome plus the sandbox size (measured after status.json is
    written), so the run page shows the size as soon as it sees the run finish."""
    try:
        outcome["disk_bytes"] = sandbox_bytes(sandbox)
    except Exception:
        pass
    try:
        await web.report(run_id, **outcome)
    except Exception as e:   # never let a failed report kill the poll loop
        print(f"[run {run_id}] could not report outcome: {e}")


async def handle(web: WebClient, job: dict):
    run, wf = job["run"], job["workflow"]
    sandbox = SANDBOX_ROOT / str(run["id"])
    sandbox.mkdir(parents=True, exist_ok=True)
    await web.report(run["id"], sandbox_path=str(sandbox))
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    task = asyncio.create_task(runner.run_workflow(wf, run, sandbox, web))
    watcher = asyncio.create_task(watch_for_stop(web, run["id"], task))
    # If the worker itself is cancelled mid-run, nothing is reported (the next startup
    # reaps the run) and status.json says so.
    status, error, outcome = "error", "worker shut down mid-run", None
    try:
        status, error, outcome = await _run_outcome(task, watcher, run["id"])
    finally:
        watcher.cancel()
        elapsed = time.monotonic() - t0
        write_status(sandbox, build_status(run["id"], status, started_at, datetime.now(timezone.utc),
                                           elapsed, error, _safe_list_files(sandbox)))
        print(f"[run {run['id']}] total running time {elapsed:.3f}s")
        if outcome is not None:
            await _final_report(web, run["id"], sandbox, outcome)


async def poll_loop(web: WebClient):
    print(f"agent worker polling {WEB_URL} every {POLL_SECONDS}s")
    try:
        while True:
            try:
                job = await web.claim_next()
            except Exception as e:
                print(f"poll failed: {e}")
                job = None
            if job is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await handle(web, job)
    finally:
        await web.aclose()


async def main():
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    web = WebClient(WEB_URL, AGENT_TOKEN)
    try:  # any run still 'running' is orphaned by a previous worker — fail it, don't leave it stuck
        n = await web.reap_running()
        if n:
            print(f"reaped {n} orphaned running run(s) -> error")
    except Exception as e:
        print(f"startup reap failed: {e}")
    # Serve the sandbox file API and run the poll loop together in one process.
    server = uvicorn.Server(uvicorn.Config(
        files_api.app, host="127.0.0.1", port=HTTP_PORT, log_level="warning"))
    print(f"sandbox file API on 127.0.0.1:{HTTP_PORT}")
    await asyncio.gather(server.serve(), poll_loop(web))


if __name__ == "__main__":
    asyncio.run(main())
