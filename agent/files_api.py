"""Read-only HTTP API over the agent's sandbox folders.

Lets the web host browse/view/download a run's files live (passthrough), so nothing is
copied back. Bearer-token auth (same AGENT_TOKEN). Exposed via the agent host's Apache
at /workflow-agent. Runs in-process with the worker (see worker.py).
"""
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse

SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", Path(__file__).parent / "sandboxes")).resolve()
AGENT_TOKEN = os.environ["AGENT_TOKEN"]

app = FastAPI(title="Agent sandbox files")


def _auth(authorization: str | None):
    if authorization != f"Bearer {AGENT_TOKEN}":
        raise HTTPException(401, "Bad agent token")


def _run_dir(run_id: int) -> Path:
    d = (SANDBOX_ROOT / str(run_id)).resolve()
    if SANDBOX_ROOT not in d.parents:          # refuse anything outside the sandbox root
        raise HTTPException(400, "Bad run id")
    return d


@app.get("/sandbox/{run_id}")
def list_files(run_id: int, authorization: str | None = Header(None)):
    _auth(authorization)
    d = _run_dir(run_id)
    if not d.is_dir():
        raise HTTPException(404, "No sandbox")
    files = [{"path": str(p.relative_to(d)), "size": p.stat().st_size}
             for p in sorted(d.rglob("*")) if p.is_file()]
    return {"files": files}


@app.get("/sandbox/{run_id}/file")
def get_file(run_id: int, path: str = Query(...), dl: int = 0,
             authorization: str | None = Header(None)):
    _auth(authorization)
    d = _run_dir(run_id)
    target = (d / path).resolve()
    if d not in target.parents:                # path traversal guard
        raise HTTPException(400, "Bad path")
    if not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target,
                        filename=target.name if dl else None,
                        content_disposition_type="attachment" if dl else "inline")


@app.delete("/sandbox/{run_id}")
def delete_sandbox(run_id: int, authorization: str | None = Header(None)):
    _auth(authorization)
    d = _run_dir(run_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}
