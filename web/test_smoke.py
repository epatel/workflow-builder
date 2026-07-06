"""Runnable smoke check for the core auth + run-queue logic.

    cd web && ../.venv/bin/python -m pytest test_smoke.py   (or just run the file)

Uses a throwaway DB so it never touches the real one.
"""
import json
import os
import tempfile

os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["AGENT_TOKEN"] = "test-agent-token"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient
import app as appmod

appmod.migrate()  # startup hook only fires under `with TestClient(...)`
client = TestClient(appmod.app)
AGENT = {"Authorization": "Bearer test-agent-token"}


def test_classify():
    assert appmod._classify("shot.PNG") == "image"
    assert appmod._classify("notes.md") == "markdown"
    assert appmod._classify("main.py") == "text"
    assert appmod._classify("page.svg") == "text"     # svg shown as code, not <img> (script safety)
    assert appmod._classify("index.html") == "text"   # html defaults to code; render is opt-in
    assert appmod._classify("sub/dir/Dockerfile") == "text"
    assert appmod._classify("data.bin") == "maybe"   # unknown -> decided by decode at view time


def test_validate_tools():
    from fastapi import HTTPException
    appmod._validate_tools('["puppeteer"]')                  # shortcut name - ok
    appmod._validate_tools('[{"name": "n", "type": "http", "url": "https://x"}]')  # inline - ok
    appmod._validate_tools("[]")                             # empty - ok
    for bad in ('not json', '{"name": "n"}',                 # not a list
                '[{"type": "http", "url": "u"}]',            # dict missing name
                '[{"name": "n", "type": "http"}]',           # http missing url
                '[123]'):                                    # not str/dict
        try:
            appmod._validate_tools(bad)
            assert False, f"expected reject: {bad}"
        except HTTPException as e:
            assert e.status_code == 400


def test_validate_inputs_spec():
    from fastapi import HTTPException
    appmod._validate_inputs_spec('[{"key": "url", "label": "URL", "type": "text"}]')  # ok
    appmod._validate_inputs_spec('[{"key": "k"}]')           # type defaults to text - ok
    appmod._validate_inputs_spec("[]")                       # empty - ok
    for bad in ('not json', '{"key": "k"}',                  # not a list
                '["k"]',                                     # item not a dict
                '[{"label": "no key"}]',                     # missing key
                '[{"key": "k", "type": "dropdown"}]',        # bad type
                '[{"key": "k"}, {"key": "k"}]'):             # duplicate key
        try:
            appmod._validate_inputs_spec(bad)
            assert False, f"expected reject: {bad}"
        except HTTPException as e:
            assert e.status_code == 400


def test_highlight():
    py = appmod._highlight("a.py", "import os\nx = 1\n")
    assert 'class="hl"' in py and "<span" in py            # python tokens highlighted
    js = appmod._highlight("d.json", '{"a": 1}')
    assert "<span" in js                                   # json highlighted too
    assert 'class="hl"' in appmod._highlight("x.unknownext", "plain words")  # falls back, still wrapped


def test_flow():
    # First signup with no invite -> becomes admin, pre-verified
    r = client.post("/signup", data={"name": "Boss", "email": "a@x.com", "password": "pw"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text

    # Second signup with no/invalid invite is rejected
    r = client.post("/signup", data={"name": "Nope", "email": "b@x.com", "password": "pw",
                                     "invite": "bad"})
    assert "Invalid or used invite code" in r.text

    # Admin logs in (cookie set)
    r = client.post("/login", data={"email": "a@x.com", "password": "pw"}, follow_redirects=False)
    assert r.status_code == 303
    assert "session" in r.cookies

    # Admin creates a 'user'-role invite, new account uses it
    client.post("/admin/invites", data={"role": "user"}, follow_redirects=False)
    code = appmod.connect().execute("SELECT code FROM invites").fetchone()["code"]
    r = client.post("/signup", data={"name": "Reg", "email": "b@x.com", "password": "pw",
                                     "invite": code}, follow_redirects=False)
    assert r.status_code == 303

    # That plain user cannot create workflows (editor+ only)
    user = TestClient(appmod.app)
    # mark verified directly (no Mailjet in test) then log in
    conn = appmod.connect()
    conn.execute("UPDATE users SET email_verified=1 WHERE email='b@x.com'"); conn.commit()
    user.post("/login", data={"email": "b@x.com", "password": "pw"})
    assert user.get("/workflows/new", follow_redirects=False).status_code == 403

    # Admin creates a workflow with one text input
    spec = '[{"key":"topic","label":"Topic","type":"text"}]'
    r = client.post("/workflows", data={"name": "WF", "inputs_spec": spec,
                    "action_prompt": "do", "eval_prompt": "sum"}, follow_redirects=False)
    wid = int(r.headers["location"].split("/")[-1])

    # Start a run
    r = client.post(f"/workflows/{wid}/run", data={"topic": "hi"}, follow_redirects=False)
    rid = int(r.headers["location"].split("/")[-1])

    # /runs lists own runs only; admin sees everyone's. This run is the admin's.
    assert f"runs/{rid}" in client.get("/runs").text                 # admin sees it
    assert f"runs/{rid}" not in user.get("/runs").text               # plain user doesn't

    # Agent claims it -> status flips to running, workflow def returned
    r = client.post("/api/runs/next", headers=AGENT)
    body = r.json()
    assert body["run"]["id"] == rid and body["run"]["status"] == "running"
    assert body["workflow"]["action_prompt"] == "do"

    # No more pending -> 204
    assert client.post("/api/runs/next", headers=AGENT).status_code == 204

    # Bad token rejected
    assert client.post("/api/runs/next", headers={"Authorization": "Bearer nope"}).status_code == 401

    # Agent posts a progress log -> visible in status
    client.post(f"/api/runs/{rid}/log", json={"message": "step one"}, headers=AGENT)
    assert any(l["message"] == "step one" for l in client.get(f"/runs/{rid}/status").json()["logs"])

    # Agent reports result -> visible via status endpoint
    client.post(f"/api/runs/{rid}", json={"status": "done", "result": "all good"}, headers=AGENT)
    st = client.get(f"/runs/{rid}/status").json()
    assert st["status"] == "done" and len(st["logs"]) == 1

    # Admin deletes the run -> gone everywhere (logs cascade); non-admin can't
    assert user.post(f"/admin/runs/{rid}/delete", follow_redirects=False).status_code == 403
    assert client.post(f"/admin/runs/{rid}/delete", follow_redirects=False).status_code == 303
    assert client.get(f"/runs/{rid}/status").status_code == 404

    # --- sets: membership controls visibility ---
    client.post("/admin/sets", data={"name": "team"}, follow_redirects=False)
    sid = appmod.connect().execute("SELECT id FROM sets WHERE name='team'").fetchone()["id"]
    ub = appmod.connect().execute("SELECT id FROM users WHERE email='b@x.com'").fetchone()["id"]
    # admin makes a workflow in 'team'
    r = client.post("/workflows", data={"name": "Shared", "inputs_spec": "[]",
                    "action_prompt": "x", "eval_prompt": "y", "sets": sid,
                    "model": "sonnet"}, follow_redirects=False)
    swid = int(r.headers["location"].split("/")[-1])
    assert "sonnet" in client.get(f"/workflows/{swid}").text                 # per-workflow model stored
    # an unknown model value is rejected to '' (account default)
    client.post(f"/workflows/{swid}", data={"name": "Shared", "inputs_spec": "[]",
                "action_prompt": "x", "eval_prompt": "y", "sets": sid, "model": "bogus"},
                follow_redirects=False)
    assert appmod.connect().execute("SELECT model FROM workflows WHERE id=?", (swid,)).fetchone()[0] == ""
    assert user.get(f"/workflows/{swid}", follow_redirects=False).status_code == 403   # not a member
    client.post(f"/admin/users/{ub}/sets", data={"sets": sid}, follow_redirects=False)  # add to set
    assert user.get(f"/workflows/{swid}", follow_redirects=False).status_code == 200    # now visible
    assert "Shared" in user.get("/workflows").text                                      # and listed

    # --- account default model resolves at claim time ---
    client.post("/account", data={"default_model": "haiku"}, follow_redirects=False)
    assert appmod.connect().execute(
        "SELECT default_model FROM users WHERE email='a@x.com'").fetchone()[0] == "haiku"
    # a bogus value is rejected to '' (host default)
    client.post("/account", data={"default_model": "nope"}, follow_redirects=False)
    assert appmod.connect().execute(
        "SELECT default_model FROM users WHERE email='a@x.com'").fetchone()[0] == ""
    # workflow with model='' inherits the run-starter's account default when claimed
    client.post("/account", data={"default_model": "haiku"}, follow_redirects=False)
    dwf = client.post("/workflows", data={"name": "Defaulted", "inputs_spec": "[]",
                      "action_prompt": "x", "eval_prompt": "y", "model": ""},
                      follow_redirects=False)
    dwid = int(dwf.headers["location"].split("/")[-1])
    client.post(f"/workflows/{dwid}/run", data={}, follow_redirects=False)
    claimed = client.post("/api/runs/next", headers=AGENT).json()
    assert claimed["workflow"]["model"] == "haiku"
    print("OK")


def test_resolve_workflow():
    # Runs after test_flow: admin (a@x.com) is logged in via module-level `client`.
    client.post("/workflows", data={"name": "Chainable", "inputs_spec": "[]",
                "action_prompt": "chain-me", "eval_prompt": "y"}, follow_redirects=False)
    starter = client.post("/workflows", data={"name": "Starter", "inputs_spec": "[]",
                          "action_prompt": "x", "eval_prompt": "y"}, follow_redirects=False)
    swid = int(starter.headers["location"].split("/")[-1])
    rid = int(client.post(f"/workflows/{swid}/run", data={},
                          follow_redirects=False).headers["location"].split("/")[-1])

    # resolve an accessible name -> 200 with the def
    r = client.get("/api/workflows/resolve", params={"name": "Chainable", "run_id": rid}, headers=AGENT)
    assert r.status_code == 200 and r.json()["action_prompt"] == "chain-me"

    # unknown name -> 404
    assert client.get("/api/workflows/resolve",
                      params={"name": "Ghost", "run_id": rid}, headers=AGENT).status_code == 404

    # ambiguous name -> 409
    for _ in range(2):
        client.post("/workflows", data={"name": "Dup", "inputs_spec": "[]",
                    "action_prompt": "x", "eval_prompt": "y"}, follow_redirects=False)
    assert client.get("/api/workflows/resolve",
                      params={"name": "Dup", "run_id": rid}, headers=AGENT).status_code == 409

    # bad agent token rejected
    assert client.get("/api/workflows/resolve", params={"name": "Chainable", "run_id": rid},
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_reap_running():
    # Runs after test_flow: admin logged in via module-level `client`.
    wf = client.post("/workflows", data={"name": "Reapable", "inputs_spec": "[]",
                     "action_prompt": "x", "eval_prompt": "y"}, follow_redirects=False)
    wid = int(wf.headers["location"].split("/")[-1])
    rid = int(client.post(f"/workflows/{wid}/run", data={},
                          follow_redirects=False).headers["location"].split("/")[-1])

    # drain the queue: claim until empty so every pending run is now 'running'
    while client.post("/api/runs/next", headers=AGENT).status_code != 204:
        pass
    assert client.get(f"/runs/{rid}/status").json()["status"] == "running"

    # reap -> our run is errored as interrupted
    assert client.post("/api/runs/reap-running", headers=AGENT).json()["reaped"] >= 1
    st = client.get(f"/runs/{rid}/status").json()
    assert st["status"] == "error" and "interrupted" in st["error"]

    # bad token rejected
    assert client.post("/api/runs/reap-running",
                       headers={"Authorization": "Bearer nope"}).status_code == 401


def test_mcp_tools():
    # migration seeded puppeteer; admin registers a tool with headers and disables another
    assert client.post("/admin/tools", data={
        "name": "browser",
        "config": '{"type": "http", "url": "http://b:1/mcp", "headers": {"Authorization": "Bearer s3cret"}}'},
        follow_redirects=False).status_code == 303
    client.post("/admin/tools", data={"name": "offtool",
                "config": '{"type": "http", "url": "http://o:1/mcp"}'}, follow_redirects=False)
    conn = appmod.connect()
    off_id = conn.execute("SELECT id FROM mcp_tools WHERE name='offtool'").fetchone()["id"]
    client.post(f"/admin/tools/{off_id}/toggle", follow_redirects=False)
    assert conn.execute("SELECT enabled FROM mcp_tools WHERE id=?", (off_id,)).fetchone()[0] == 0

    # bad configs rejected: non-JSON, bad scheme, bad type, name inside config
    for bad in ["not json", '{"type": "http", "url": "ftp://nope"}',
                '{"type": "stdio", "url": "http://x"}',
                '{"name": "x", "type": "http", "url": "http://x"}']:
        assert client.post("/admin/tools", data={"name": "x", "config": bad},
                           follow_redirects=False).status_code == 400, bad

    # config is editable in place (e.g. rotating a bearer token)
    b_id = conn.execute("SELECT id FROM mcp_tools WHERE name='browser'").fetchone()["id"]
    assert client.post(f"/admin/tools/{b_id}", data={
        "config": '{"type": "http", "url": "http://b:1/mcp", "headers": {"Authorization": "Bearer rotated"}}'},
        follow_redirects=False).status_code == 303
    assert "rotated" in conn.execute("SELECT config FROM mcp_tools WHERE id=?", (b_id,)).fetchone()[0]
    assert client.post(f"/admin/tools/{b_id}", data={"config": "junk"},
                       follow_redirects=False).status_code == 400

    # workflow naming enabled/disabled/unknown/inline tools
    tools = '["browser", "offtool", "mystery", {"name": "inline", "type": "http", "url": "https://i/mcp"}]'
    r = client.post("/workflows", data={"name": "Tooled", "inputs_spec": "[]",
                    "action_prompt": "x", "eval_prompt": "", "tools": tools}, follow_redirects=False)
    twid = int(r.headers["location"].split("/")[-1])
    rid = int(client.post(f"/workflows/{twid}/run", data={}, follow_redirects=False)
              .headers["location"].split("/")[-1])

    # claim: enabled name -> inline dict (extra config keys ride along); disabled dropped
    # (+ run log note); unknown passes through
    body = client.post("/api/runs/next", headers=AGENT).json()
    resolved = json.loads(body["workflow"]["tools"])
    assert {"name": "browser", "type": "http", "url": "http://b:1/mcp",
            "headers": {"Authorization": "Bearer rotated"}} in resolved
    assert "mystery" in resolved
    assert {"name": "inline", "type": "http", "url": "https://i/mcp"} in resolved
    assert "offtool" not in json.dumps(resolved)
    logs = client.get(f"/runs/{rid}/status").json()["logs"]
    assert any("offtool" in l["message"] and "disabled" in l["message"] for l in logs)

    # edit page's "Available tools" hint lists enabled tool names only
    page = client.get(f"/workflows/{twid}/edit").text
    assert '<code>"browser"</code>' in page and '<code>"offtool"</code>' not in page

    # deleting a tool makes its name pass through untouched (agent fallback may know it)
    client.post(f"/admin/tools/{off_id}/delete", follow_redirects=False)
    assert conn.execute("SELECT COUNT(*) c FROM mcp_tools WHERE id=?", (off_id,)).fetchone()["c"] == 0


if __name__ == "__main__":
    test_classify()
    test_highlight()
    test_flow()
    test_resolve_workflow()
    test_reap_running()
    test_mcp_tools()
