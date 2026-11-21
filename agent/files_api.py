"""HTTP API the web host calls on the agent host.

Mostly read-only access to the agent's sandbox folders, so the web host can
browse/view/download a run's files live (passthrough) without copying anything back.
Also probes an MCP server's reachability (`/mcp/test`) — that has to run here because
the agent host, not the web host, is what has to reach the tool (e.g. puppeteer on the
LAN). Bearer-token auth (same AGENT_TOKEN). Exposed via the agent host's Apache at
/workflow-agent. Runs in-process with the worker (see worker.py).
"""
import json
import os
import shutil
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Query
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


def parse_rpc_body(content_type: str, text: str) -> dict:
    """Pull the JSON-RPC message out of an MCP reply. Streamable-HTTP servers answer
    either with plain JSON or with a one-message SSE stream, so accept both. Pure —
    unit tested."""
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("event stream carried no data line")
    return json.loads(text)


async def _probe_http(cfg: dict, timeout: float) -> dict:
    """MCP initialize (+ tools/list when the server lets us) against a streamable-HTTP
    server. Returns the payload for a successful test."""
    headers = dict(cfg.get("headers") or {})
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json, text/event-stream")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "workflow-agent", "version": "1.0"}}}
    async with httpx.AsyncClient(timeout=timeout) as http:
        r = await http.post(cfg["url"], headers=headers, json=init)
        if r.status_code >= 400:
            raise ValueError(f"initialize returned HTTP {r.status_code}: {r.text[:200]}")
        msg = parse_rpc_body(r.headers.get("content-type", ""), r.text)
        if "error" in msg:
            raise ValueError(f"initialize failed: {msg['error']}")
        info = (msg.get("result") or {}).get("serverInfo") or {}
        session = r.headers.get("mcp-session-id")
        if session:
            headers["Mcp-Session-Id"] = session
        # tools/list is a bonus: some servers require the initialized notification or
        # a session we don't carry, so a failure here doesn't fail the test.
        tools = None
        try:
            await http.post(cfg["url"], headers=headers,
                            json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            r2 = await http.post(cfg["url"], headers=headers,
                                 json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = parse_rpc_body(r2.headers.get("content-type", ""), r2.text)
            tools = [t["name"] for t in (listed.get("result") or {}).get("tools", [])]
        except Exception:
            tools = None
    return {"ok": True, "server": info.get("name") or "", "version": info.get("version") or "",
            "tools": tools}


async def _probe_sse(cfg: dict, timeout: float) -> dict:
    """Legacy SSE transport: we can only check that the event stream opens."""
    headers = dict(cfg.get("headers") or {})
    headers.setdefault("Accept", "text/event-stream")
    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream("GET", cfg["url"], headers=headers) as r:
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "")
            if "text/event-stream" not in ctype:
                raise ValueError(f"not an event stream (content-type: {ctype or 'none'})")
    return {"ok": True, "server": "", "version": "", "tools": None}


@app.post("/mcp/test")
async def test_mcp(config: dict = Body(...), authorization: str | None = Header(None)):
    """Check that this host can actually reach an MCP server. `config` is the inline
    server dict a workflow would get (type/url/headers, no name). Never raises for a
    failed probe — the failure is the answer, returned as {"ok": false, "error": ...}."""
    _auth(authorization)
    url = config.get("url")
    if config.get("type") not in ("http", "sse") or not isinstance(url, str):
        raise HTTPException(400, 'config needs "type" http|sse and a "url"')
    timeout = float(os.getenv("MCP_TEST_TIMEOUT", "15"))
    try:
        probe = _probe_sse if config["type"] == "sse" else _probe_http
        return await probe(config, timeout)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}".strip(": ")}


@app.get("/sandboxes")
def list_sandboxes(authorization: str | None = Header(None)):
    """Run ids that have a sandbox on this host — the web side diffs it against its runs
    table to find orphans (sandboxes whose run row is gone)."""
    _auth(authorization)
    if not SANDBOX_ROOT.is_dir():
        return {"run_ids": []}
    return {"run_ids": sorted(int(d.name) for d in SANDBOX_ROOT.iterdir()
                              if d.is_dir() and d.name.isdigit())}


@app.delete("/sandbox/{run_id}")
def delete_sandbox(run_id: int, authorization: str | None = Header(None)):
    _auth(authorization)
    d = _run_dir(run_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}
