"""Deeper logic tests beyond the smoke flow.

These target invariants a happy-path test would miss: the set-authorization
rule that stops an editor clobbering admin-assigned sets, the single-use /
expiry contract of reset tokens, upload-filename sanitization (path traversal),
and age-based run purging.

    cd web && ../.venv/bin/python test_logic.py   (or: python -m pytest test_logic.py)

Uses a throwaway DB + uploads dir so it never touches the real ones.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["AGENT_TOKEN"] = "test-agent-token"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["UPLOADS_DIR"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient
import app as appmod

appmod.migrate()
appmod.UPLOADS.mkdir(parents=True, exist_ok=True)
client = TestClient(appmod.app)


def _mkuser(conn, email, role):
    return conn.execute(
        "INSERT INTO users (email,name,pw_hash,role,email_verified) VALUES (?,?,?,?,1)",
        (email, "n", appmod.hash_password("pw"), role)).lastrowid


def test_save_workflow_sets_authorization():
    """An editor may only touch sets they belong to: dropping a set they manage
    works, but an admin-assigned set they aren't in is preserved (not clobbered),
    and they cannot add themselves to a set they aren't a member of."""
    conn = appmod.connect()
    editor_id = _mkuser(conn, "editor@x.com", "editor")
    a = conn.execute("INSERT INTO sets (name) VALUES ('A')").lastrowid   # editor is in A
    b = conn.execute("INSERT INTO sets (name) VALUES ('B')").lastrowid   # admin-only (locked)
    c = conn.execute("INSERT INTO sets (name) VALUES ('C')").lastrowid   # editor not a member
    conn.execute("INSERT INTO user_sets (user_id,set_id) VALUES (?,?)", (editor_id, a))
    wid = conn.execute("INSERT INTO workflows (owner_id,name) VALUES (?,'wf')",
                       (editor_id,)).lastrowid
    conn.executemany("INSERT INTO workflow_sets (workflow_id,set_id) VALUES (?,?)",
                     [(wid, a), (wid, b)])      # workflow starts in A and the locked B
    conn.commit()
    editor = conn.execute("SELECT * FROM users WHERE id=?", (editor_id,)).fetchone()

    # Editor asks to set the workflow's sets to {C} only — drop A, drop B, add C.
    appmod._save_workflow_sets(conn, wid, {c}, editor)
    conn.commit()

    result = appmod._workflow_set_ids(conn, wid)
    assert a not in result, "editor manages A and chose to drop it -> should be gone"
    assert b in result,     "B is admin-locked (editor not a member) -> must be preserved"
    assert c not in result, "editor isn't in C -> must not be able to add it"
    conn.close()


def test_admin_save_workflow_sets_is_unrestricted():
    """Admins can assign any set; nothing is 'locked' from them."""
    conn = appmod.connect()
    admin_id = _mkuser(conn, "admin-sets@x.com", "admin")
    x = conn.execute("INSERT INTO sets (name) VALUES ('X')").lastrowid
    y = conn.execute("INSERT INTO sets (name) VALUES ('Y')").lastrowid
    wid = conn.execute("INSERT INTO workflows (owner_id,name) VALUES (?,'wf2')",
                       (admin_id,)).lastrowid
    conn.commit()
    admin = conn.execute("SELECT * FROM users WHERE id=?", (admin_id,)).fetchone()

    appmod._save_workflow_sets(conn, wid, {x, y}, admin)   # admin in no set, yet may assign both
    conn.commit()
    assert appmod._workflow_set_ids(conn, wid) == {x, y}
    conn.close()


def test_reset_token_is_single_use_and_expires():
    """_consume_token enforces single-use, kind-match, and expiry."""
    conn = appmod.connect()
    uid = _mkuser(conn, "reset@x.com", "user")
    conn.commit()

    tok = appmod._new_token(conn, uid, "reset")
    conn.commit()
    assert appmod._consume_token(conn, tok, "verify") is None   # wrong kind -> rejected
    assert appmod._consume_token(conn, tok, "reset") == uid     # right kind -> consumed
    conn.commit()
    assert appmod._consume_token(conn, tok, "reset") is None    # already used -> rejected

    # An expired token is rejected even though unused.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("INSERT INTO tokens (user_id,kind,token,expires_at) VALUES (?,?,?,?)",
                 (uid, "reset", "stale-token", past))
    conn.commit()
    assert appmod._consume_token(conn, "stale-token", "reset") is None
    conn.close()


def test_password_reset_flow_end_to_end():
    """forgot -> reset -> the old password stops working and the new one logs in."""
    conn = appmod.connect()
    uid = _mkuser(conn, "flow@x.com", "user")
    conn.commit()
    conn.close()

    client.post("/forgot-password", data={"email": "flow@x.com"})
    tok = appmod.connect().execute(
        "SELECT token FROM tokens WHERE user_id=? AND kind='reset' ORDER BY id DESC", (uid,)
    ).fetchone()["token"]

    r = client.post("/reset-password", data={"token": tok, "password": "newpw"},
                    follow_redirects=False)
    assert r.status_code == 303

    # Old password is dead, new one works.
    assert client.post("/login", data={"email": "flow@x.com", "password": "pw"},
                       follow_redirects=False).status_code != 303
    assert client.post("/login", data={"email": "flow@x.com", "password": "newpw"},
                       follow_redirects=False).status_code == 303


def test_upload_filename_is_sanitized_to_basename():
    """A run's file input must land in the run's own upload dir as a bare
    basename — a traversal-laden filename must not escape it."""
    conn = appmod.connect()
    _mkuser(conn, "owner@x.com", "editor")
    conn.commit()
    conn.close()
    up = TestClient(appmod.app)
    up.post("/login", data={"email": "owner@x.com", "password": "pw"})
    spec = '[{"key":"doc","label":"Doc","type":"file"}]'
    r = up.post("/workflows", data={"name": "Up", "inputs_spec": spec,
                "action_prompt": "do", "eval_prompt": ""}, follow_redirects=False)
    wid = int(r.headers["location"].split("/")[-1])

    evil = ("doc", ("../../pwned.txt", b"payload", "text/plain"))
    r = up.post(f"/workflows/{wid}/run", files=[evil], follow_redirects=False)
    rid = int(r.headers["location"].split("/")[-1])

    import json
    stored = json.loads(appmod.connect().execute(
        "SELECT inputs FROM runs WHERE id=?", (rid,)).fetchone()["inputs"])
    assert stored["doc"] == {"file": "pwned.txt"}                       # dirs stripped
    assert (appmod.UPLOADS / str(rid) / "pwned.txt").is_file()          # inside the run dir
    assert not (appmod.UPLOADS.parent / "pwned.txt").exists()           # nothing escaped


def test_admin_purge_runs_by_age():
    """Purge deletes only runs older than the cutoff; recent ones survive."""
    conn = appmod.connect()
    admin_id = _mkuser(conn, "purger@x.com", "admin")
    wid = conn.execute("INSERT INTO workflows (owner_id,name) VALUES (?,'pw-wf')",
                       (admin_id,)).lastrowid
    old = conn.execute(
        "INSERT INTO runs (workflow_id,user_id,created_at) VALUES (?,?,datetime('now','-30 days'))",
        (wid, admin_id)).lastrowid
    fresh = conn.execute(
        "INSERT INTO runs (workflow_id,user_id,created_at) VALUES (?,?,datetime('now'))",
        (wid, admin_id)).lastrowid
    conn.commit()
    conn.close()

    admin_client = TestClient(appmod.app)
    admin_client.post("/login", data={"email": "purger@x.com", "password": "pw"})
    r = admin_client.post("/admin/runs/purge", data={"days": 7}, follow_redirects=False)
    assert r.status_code == 303

    rows = {x["id"] for x in appmod.connect().execute("SELECT id FROM runs").fetchall()}
    assert old not in rows and fresh in rows


if __name__ == "__main__":
    test_save_workflow_sets_authorization()
    test_admin_save_workflow_sets_is_unrestricted()
    test_reset_token_is_single_use_and_expires()
    test_password_reset_flow_end_to_end()
    test_upload_filename_is_sanitized_to_basename()
    test_admin_purge_runs_by_age()
    print("OK")
