"""Runnable check for the agent's pure logic + worker report loop (no real SDK call).

    cd agent && ../agent/.venv/bin/python test_agent.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault("AGENT_TOKEN", "t")
os.environ["SANDBOX_ROOT"] = tempfile.mkdtemp()

import runner
import worker


def test_files_api():
    from fastapi.testclient import TestClient
    import files_api
    sb = Path(os.environ["SANDBOX_ROOT"]) / "5"
    (sb / "sub").mkdir(parents=True)
    (sb / "result.txt").write_text("hello")
    (sb / "sub" / "data.csv").write_text("a,b")
    c = TestClient(files_api.app)
    auth = {"Authorization": "Bearer t"}

    assert c.get("/sandbox/5").status_code == 401                 # no token rejected
    listing = c.get("/sandbox/5", headers=auth).json()["files"]
    paths = {f["path"] for f in listing}
    assert "result.txt" in paths and "sub/data.csv" in paths      # recursive listing
    assert c.get("/sandbox/5/file", params={"path": "result.txt"}, headers=auth).text == "hello"
    assert c.get("/sandbox/999", headers=auth).status_code == 404  # missing sandbox
    # path traversal is refused
    assert c.get("/sandbox/5/file", params={"path": "../../etc/passwd"}, headers=auth).status_code == 400
    # delete removes the sandbox
    assert c.delete("/sandbox/5", headers=auth).status_code == 200
    assert not sb.exists()
    assert c.get("/sandbox/5", headers=auth).status_code == 404


def test_tools_config():
    mcp, allowed = runner.tools_config(["puppeteer", "unknown"])
    assert mcp == {"puppeteer": {"type": "http", "url": runner.PUPPETEER_MCP_URL}}
    assert "mcp__puppeteer" in allowed
    assert all(t in allowed for t in runner.BASE_TOOLS)   # base tools always present
    assert "mcp__unknown" not in allowed                  # unknown tools dropped

    mcp, allowed = runner.tools_config([])                # no tools -> no mcp servers
    assert mcp == {} and allowed == runner.BASE_TOOLS

    # inline server dict: name stripped into the server key, rest passed through
    mcp, allowed = runner.tools_config([{"name": "nnn", "type": "http", "url": "https://x/mcp"}])
    assert mcp == {"nnn": {"type": "http", "url": "https://x/mcp"}}
    assert "mcp__nnn" in allowed


def test_compose_prompt():
    p = runner.compose_prompt("Do the thing", {"topic": "cats", "doc": {"file": "a.csv"}}, ["a.csv"])
    assert "Do the thing" in p
    assert "- topic: cats" in p           # text input shown
    assert "doc:" not in p                # file input not dumped as text
    assert "a.csv" in p                   # file listed in the files line


class ChainWeb:
    """FakeWeb for the chain loop: scripted name->workflow resolution + captured logs."""
    def __init__(self, defs): self.defs, self.logs, self.resolved, self.steps = defs, [], [], []
    async def log(self, run_id, msg): self.logs.append(msg)
    async def download_file(self, *a): pass
    async def resolve_workflow(self, name, run_id):
        self.resolved.append(name)
        return self.defs.get(name)


def _run_chain(routes, defs):
    """Drive run_workflow with a fake _run_step that emits routes[step] into _CTX['next']."""
    web = ChainWeb(defs)
    seq = list(routes)

    async def fake_step(wf, sandbox, inputs, files, previous_result, previous_name, handover):
        web.steps.append({"name": wf["name"], "previous_name": previous_name,
                          "handover": handover, "inputs": inputs})
        nxt = seq.pop(0) if seq else None
        if nxt:
            runner._CTX["next"] = nxt
            runner._CTX["handover"] = f"note for {nxt}"
            runner._CTX["handover_inputs"] = {"to": nxt}
        return f"result-{wf['name']}"

    orig = runner._run_step
    runner._run_step = fake_step
    try:
        sandbox = Path(tempfile.mkdtemp())
        run = {"id": 1, "inputs": "{}"}
        result = asyncio.run(runner.run_workflow({"name": "A"}, run, sandbox, web))
    finally:
        runner._run_step = orig
    return result, web


def test_chain_loop():
    # B and C declare the "to" input so the handover value survives the drop-undeclared filter.
    defs = {"B": {"name": "B", "inputs_spec": '[{"key":"to"}]'},
            "C": {"name": "C", "inputs_spec": '[{"key":"to"}]'}}
    # (a) A -> B -> C, then stop
    result, web = _run_chain(["B", "C", None], defs)
    assert result == "result-C"
    assert web.resolved == ["B", "C"]
    # handover note + source name + filled inputs thread to the next step
    assert web.steps[1]["previous_name"] == "A" and web.steps[1]["handover"] == "note for B"
    assert web.steps[1]["inputs"] == {"to": "B"}          # declared handover input threads through
    assert web.steps[2]["previous_name"] == "B" and web.steps[2]["handover"] == "note for C"
    assert web.steps[0]["inputs"] == {}                   # first step keeps its own (empty) inputs

    # (b) no routing -> single step
    result, web = _run_chain([None], defs)
    assert result == "result-A" and web.resolved == []

    # (c) resolve miss -> chain ends gracefully on the current result
    result, web = _run_chain(["nope"], {})
    assert result == "result-A"
    assert any("could not resolve" in m for m in web.logs)

    # (d) step cap guards cycles (self-route forever)
    orig_max = runner.MAX_CHAIN_STEPS
    runner.MAX_CHAIN_STEPS = 3
    try:
        result, web = _run_chain(["A", "A", "A", "A", "A"], {"A": {"name": "A"}})
    finally:
        runner.MAX_CHAIN_STEPS = orig_max
    assert web.resolved == ["A", "A"]            # ran 3 steps, routed twice, then capped
    assert any("cap" in m for m in web.logs)


class FakeWeb:
    def __init__(self): self.reports = []
    async def report(self, run_id, **f): self.reports.append(f)


def test_handle_success(monkeypatch_result="summary", should_fail=False):
    web = FakeWeb()

    async def fake_run(wf, run, sandbox, w):
        if should_fail:
            raise RuntimeError("boom")
        return monkeypatch_result

    runner_orig = worker.runner.run_workflow
    worker.runner.run_workflow = fake_run
    worker.SANDBOX_ROOT = Path("/tmp/wf-test-sandboxes")
    try:
        job = {"run": {"id": 7, "inputs": "{}"}, "workflow": {}}
        asyncio.run(worker.handle(web, job))
    finally:
        worker.runner.run_workflow = runner_orig

    statuses = [r.get("status") for r in web.reports]
    if should_fail:
        assert "error" in statuses and web.reports[-1]["error"] == "boom"
    else:
        assert "done" in statuses and web.reports[-1]["result"] == "summary"

    # status.json is written to the sandbox with the run's total running time.
    import json
    status_file = worker.SANDBOX_ROOT / "7" / "status.json"
    assert status_file.exists()
    data = json.loads(status_file.read_text())
    assert data["run_id"] == 7
    assert data["status"] == ("error" if should_fail else "done")
    assert isinstance(data["total_seconds"], (int, float)) and data["total_seconds"] >= 0
    assert data["started_at"] and data["finished_at"]
    if should_fail:
        assert data["error"] == "boom"
    else:
        assert "error" not in data


def test_build_status():
    from datetime import datetime, timezone
    t0 = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 7, 12, 0, 5, tzinfo=timezone.utc)
    ok = worker.build_status(3, "done", t0, t1, 5.1234)
    assert ok == {"run_id": 3, "status": "done", "started_at": t0.isoformat(),
                  "finished_at": t1.isoformat(), "total_seconds": 5.123}
    assert "error" not in ok                               # no error key on success
    bad = worker.build_status(4, "error", t0, t1, 1.0, "boom")
    assert bad["error"] == "boom" and bad["status"] == "error"


if __name__ == "__main__":
    test_tools_config()
    test_compose_prompt()
    test_chain_loop()
    test_files_api()
    test_build_status()
    test_handle_success()                       # success path
    test_handle_success(should_fail=True)       # failure path reports error
    print("OK")
