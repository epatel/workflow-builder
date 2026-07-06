"""Workflow Builder — web server: accounts, workflow CRUD, run queue API."""
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import markdown as md
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
from pygments.util import ClassNotFound
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
# request.form() yields starlette's UploadFile, which is NOT an instance of fastapi's
# UploadFile subclass — isinstance against the fastapi one silently drops every upload.
from starlette.datastructures import UploadFile

load_dotenv()

from db import connect, migrate
from auth import (hash_password, verify_password, make_session, COOKIE,
                  current_user, optional_user, require_role)
import email_service

BASE = Path(__file__).parent
UPLOADS = Path(os.getenv("UPLOADS_DIR", BASE / "uploads"))
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")
# Agent host's sandbox file API, reachable from the web host (e.g. https://home.memention.net/workflow-agent).
AGENT_FILES_URL = os.getenv("AGENT_FILES_URL", "")
TOKEN_TTL_HOURS = 24

app = FastAPI(title="Workflow Builder")
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
def _startup():
    migrate()
    UPLOADS.mkdir(parents=True, exist_ok=True)


def page(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def _prefix(request) -> str:
    """Public path prefix (e.g. '/workflow' behind Apache, '' in local dev)."""
    return request.scope.get("root_path", "")


def redirect(request, path: str, code: int = 303) -> RedirectResponse:
    """Redirect to an app-internal path, prefixed for sub-path hosting."""
    return RedirectResponse(_prefix(request) + path, status_code=code)


def _new_token(conn, user_id, kind):
    tok = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).isoformat()
    conn.execute("INSERT INTO tokens (user_id, kind, token, expires_at) VALUES (?,?,?,?)",
                 (user_id, kind, tok, exp))
    return tok


def _consume_token(conn, token, kind):
    row = conn.execute(
        "SELECT * FROM tokens WHERE token=? AND kind=? AND used=0", (token, kind)).fetchone()
    if row is None or row["expires_at"] < datetime.now(timezone.utc).isoformat():
        return None
    conn.execute("UPDATE tokens SET used=1 WHERE id=?", (row["id"],))
    return row["user_id"]


# ---------- accounts ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(optional_user)):
    if user is None:
        return redirect(request, "/login")
    return redirect(request, "/workflows")


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    conn = connect()
    try:
        first = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
    finally:
        conn.close()
    return page(request, "signup.html", first=first, error=None)


@app.post("/signup")
def signup(request: Request, email: str = Form(...), name: str = Form(...),
           password: str = Form(...), invite: str = Form("")):
    conn = connect()
    try:
        first = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
        role = "admin"
        invite_row = None
        if not first:
            invite_row = conn.execute(
                "SELECT * FROM invites WHERE code=? AND used_by IS NULL", (invite,)).fetchone()
            if invite_row is None:
                return page(request, "signup.html", first=False,
                            error="Invalid or used invite code.")
            role = invite_row["role"]
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return page(request, "signup.html", first=first, error="Email already registered.")
        cur = conn.execute(
            "INSERT INTO users (email, name, pw_hash, role, email_verified) VALUES (?,?,?,?,?)",
            (email, name, hash_password(password), role, 1 if first else 0))
        uid = cur.lastrowid
        if invite_row:
            conn.execute("UPDATE invites SET used_by=? WHERE id=?", (uid, invite_row["id"]))
            # new account joins every set configured on the invite
            conn.executemany(
                "INSERT OR IGNORE INTO user_sets (user_id, set_id) "
                "SELECT ?, set_id FROM invite_sets WHERE invite_id=?", [(uid, invite_row["id"])])
        if not first:
            tok = _new_token(conn, uid, "verify")
            conn.commit()
            email_service.send_verification_email(email, name, tok)
        else:
            conn.commit()  # first user is admin + pre-verified
        return redirect(request, "/login?msg=Account+created")
    finally:
        conn.close()


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str):
    conn = connect()
    try:
        uid = _consume_token(conn, token, "verify")
        if uid:
            conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (uid,))
            conn.commit()
        return page(request, "message.html",
                    title="Email verification",
                    body="Email verified — you can log in." if uid else "Invalid or expired link.")
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, msg: str = "", error: str = ""):
    return page(request, "login.html", msg=msg, error=error)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = connect()
    try:
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()
    if not u or not verify_password(password, u["pw_hash"]):
        return page(request, "login.html", msg="", error="Wrong email or password.")
    if u["status"] == "blocked":
        return page(request, "login.html", msg="", error="Account is blocked.")
    if not u["email_verified"]:
        return page(request, "login.html", msg="", error="Please verify your email first.")
    resp = redirect(request, "/workflows")
    resp.set_cookie(COOKIE, make_session(u["id"]), httponly=True, samesite="lax",
                    path=_prefix(request) or "/")
    return resp


@app.post("/logout")
def logout(request: Request):
    resp = redirect(request, "/login")
    resp.delete_cookie(COOKIE, path=_prefix(request) or "/")
    return resp


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_form(request: Request):
    return page(request, "forgot.html", msg=None)


@app.post("/forgot-password")
def forgot(request: Request, email: str = Form(...)):
    conn = connect()
    try:
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u:
            tok = _new_token(conn, u["id"], "reset")
            conn.commit()
            email_service.send_password_reset_email(email, tok)
    finally:
        conn.close()
    # Don't reveal whether the email exists.
    return page(request, "forgot.html", msg="If that email exists, a reset link was sent.")


@app.get("/reset-password", response_class=HTMLResponse)
def reset_form(request: Request, token: str):
    return page(request, "reset.html", token=token, error=None)


@app.post("/reset-password")
def reset(request: Request, token: str = Form(...), password: str = Form(...)):
    conn = connect()
    try:
        uid = _consume_token(conn, token, "reset")
        if not uid:
            return page(request, "reset.html", token=token, error="Invalid or expired link.")
        conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), uid))
        conn.commit()
        return redirect(request, "/login?msg=Password+updated")
    finally:
        conn.close()


# ---------- admin ----------

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, user=Depends(require_role("admin"))):
    conn = connect()
    try:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        invites = conn.execute("SELECT * FROM invites ORDER BY id DESC").fetchall()
        sets = conn.execute("SELECT * FROM sets ORDER BY name").fetchall()
        runs = conn.execute(
            "SELECT r.id, r.status, r.created_at, w.name AS wf, u.email AS who "
            "FROM runs r JOIN workflows w ON w.id=r.workflow_id JOIN users u ON u.id=r.user_id "
            "ORDER BY r.id DESC LIMIT 100").fetchall()
        run_total = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
        # surface each tool's url for the list view; the full config lives in the edit dialog
        mcp_tools = [dict(t) | {"url": json.loads(t["config"]).get("url", "")}
                     for t in conn.execute("SELECT * FROM mcp_tools ORDER BY name")]
        # set memberships keyed by user id / invite id, for rendering checkboxes
        user_sets = {u["id"]: set() for u in users}
        for r in conn.execute("SELECT user_id, set_id FROM user_sets"):
            user_sets.setdefault(r["user_id"], set()).add(r["set_id"])
        invite_sets = {i["id"]: set() for i in invites}
        for r in conn.execute("SELECT invite_id, set_id FROM invite_sets"):
            invite_sets.setdefault(r["invite_id"], set()).add(r["set_id"])
        set_name = {s["id"]: s["name"] for s in sets}
    finally:
        conn.close()
    return page(request, "admin.html", user=user, users=users, invites=invites, sets=sets,
                runs=runs, run_total=run_total, user_sets=user_sets, invite_sets=invite_sets,
                set_name=set_name, mcp_tools=mcp_tools)


def _validate_tool_config(config: str) -> str:
    """Check a tool's server config JSON and return it normalized. It is the inline
    server dict minus "name": needs type http|sse and an http(s) url; extra keys
    (e.g. {"headers": {"Authorization": "Bearer ..."}}) pass through to the SDK."""
    try:
        cfg = json.loads(config or "")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Config must be valid JSON: {e}")
    if not isinstance(cfg, dict):
        raise HTTPException(400, 'Config must be a JSON object, e.g. {"type": "http", "url": "..."}')
    if cfg.get("type") not in ("http", "sse"):
        raise HTTPException(400, 'Config "type" must be "http" or "sse"')
    if not isinstance(cfg.get("url"), str) or not cfg["url"].startswith(("http://", "https://")):
        raise HTTPException(400, 'Config needs an http(s) "url"')
    if "name" in cfg:
        raise HTTPException(400, 'Leave "name" out of the config — the tool name is the name')
    return json.dumps(cfg)


@app.post("/admin/tools")
def create_tool(request: Request, name: str = Form(...), config: str = Form(...),
                user=Depends(require_role("admin"))):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Tool needs a name")
    config = _validate_tool_config(config)
    conn = connect()
    try:
        conn.execute("INSERT OR IGNORE INTO mcp_tools (name, config) VALUES (?,?)", (name, config))
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/tools/{tid}")
def update_tool(request: Request, tid: int, config: str = Form(...),
                user=Depends(require_role("admin"))):
    config = _validate_tool_config(config)
    conn = connect()
    try:
        conn.execute("UPDATE mcp_tools SET config=? WHERE id=?", (config, tid))
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/tools/{tid}/toggle")
def toggle_tool(request: Request, tid: int, user=Depends(require_role("admin"))):
    conn = connect()
    try:
        conn.execute("UPDATE mcp_tools SET enabled = 1 - enabled WHERE id=?", (tid,))
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/tools/{tid}/delete")
def delete_tool(request: Request, tid: int, user=Depends(require_role("admin"))):
    conn = connect()
    try:
        conn.execute("DELETE FROM mcp_tools WHERE id=?", (tid,))
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/sets")
def create_set(request: Request, name: str = Form(...), user=Depends(require_role("admin"))):
    name = name.strip()
    if name:
        conn = connect()
        try:
            conn.execute("INSERT OR IGNORE INTO sets (name) VALUES (?)", (name,))
            conn.commit()
        finally:
            conn.close()
    return redirect(request, "/admin")


@app.post("/admin/sets/{sid}/delete")
def delete_set(request: Request, sid: int, user=Depends(require_role("admin"))):
    conn = connect()
    try:
        conn.execute("DELETE FROM sets WHERE id=?", (sid,))  # memberships/assignments cascade
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/users/{uid}/sets")
def update_user_sets(request: Request, uid: int, sets: list[int] = Form([]),
                     user=Depends(require_role("admin"))):
    conn = connect()
    try:
        conn.execute("DELETE FROM user_sets WHERE user_id=?", (uid,))
        conn.executemany("INSERT INTO user_sets (user_id, set_id) VALUES (?,?)",
                         [(uid, sid) for sid in sets])
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


def _delete_run(conn, rid: int):
    """Delete a run everywhere: sandbox on the agent host, uploaded inputs, DB row (+logs cascade)."""
    if AGENT_FILES_URL:
        try:
            _agent_files("DELETE", f"/sandbox/{rid}")   # best-effort; row goes regardless
        except Exception:
            pass
    shutil.rmtree(UPLOADS / str(rid), ignore_errors=True)
    conn.execute("DELETE FROM runs WHERE id=?", (rid,))  # run_logs cascade (FK on)


@app.post("/admin/runs/{rid}/delete")
def admin_delete_run(request: Request, rid: int, user=Depends(require_role("admin"))):
    conn = connect()
    try:
        _delete_run(conn, rid)
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/runs/purge")
def admin_purge_runs(request: Request, days: int = Form(...), user=Depends(require_role("admin"))):
    """Delete every run older than `days` days (and its logs + sandbox)."""
    conn = connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM runs WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",)).fetchall()]
        for rid in ids:
            _delete_run(conn, rid)
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/invites")
def create_invite(request: Request, role: str = Form("user"), sets: list[int] = Form([]),
                  user=Depends(require_role("admin"))):
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO invites (code, role, created_by) VALUES (?,?,?)",
                           (secrets.token_urlsafe(8), role, user["id"]))
        conn.executemany("INSERT INTO invite_sets (invite_id, set_id) VALUES (?,?)",
                         [(cur.lastrowid, sid) for sid in sets])
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


@app.post("/admin/users/{uid}/{action}")
def admin_user(request: Request, uid: int, action: str, user=Depends(require_role("admin"))):
    if uid == user["id"]:
        raise HTTPException(400, "Cannot modify your own account here.")
    conn = connect()
    try:
        if action == "block":
            conn.execute("UPDATE users SET status='blocked' WHERE id=?", (uid,))
        elif action == "unblock":
            conn.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
        elif action == "delete":
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
        else:
            raise HTTPException(400, "Unknown action")
        conn.commit()
    finally:
        conn.close()
    return redirect(request, "/admin")


# ---------- sets (workflow/user grouping) ----------

def _assignable_set_ids(conn, user) -> set:
    """Sets a user may put their workflows in / browse: admin = all, else their memberships."""
    if user["role"] == "admin":
        return {r["id"] for r in conn.execute("SELECT id FROM sets")}
    return {r["set_id"] for r in
            conn.execute("SELECT set_id FROM user_sets WHERE user_id=?", (user["id"],))}


def _workflow_set_ids(conn, wid) -> set:
    return {r["set_id"] for r in
            conn.execute("SELECT set_id FROM workflow_sets WHERE workflow_id=?", (wid,))}


def _save_workflow_sets(conn, wid, requested_ids, user):
    """Set the workflow's sets to the requested ones the user may use, preserving any
    existing sets the user can't manage (e.g. an admin-assigned set the editor isn't in)."""
    allowed = _assignable_set_ids(conn, user)
    keep = {sid for sid in requested_ids if sid in allowed}
    locked = _workflow_set_ids(conn, wid) - allowed     # don't clobber what the user can't see
    conn.execute("DELETE FROM workflow_sets WHERE workflow_id=?", (wid,))
    conn.executemany("INSERT INTO workflow_sets (workflow_id, set_id) VALUES (?,?)",
                     [(wid, sid) for sid in (keep | locked)])


def _set_names(conn, ids) -> list[str]:
    if not ids:
        return []
    q = "SELECT name FROM sets WHERE id IN (%s) ORDER BY name" % ",".join("?" * len(ids))
    return [r["name"] for r in conn.execute(q, tuple(ids))]


# ---------- workflows ----------

# (value, label) — value is passed to the Agent SDK / Claude Code CLI as --model.
# "" means use the agent host's default. Aliases resolve to the latest of each tier.
_MODEL_TIERS = [
    ("fable", "Fable — most capable"),
    ("opus", "Opus — powerful"),
    ("sonnet", "Sonnet — balanced"),
    ("haiku", "Haiku — fastest"),
]
# Workflow picker: '' falls back to the run-starter's account default (see resolve at claim time).
MODEL_CHOICES = [("", "Account default")] + _MODEL_TIERS
_MODEL_VALUES = {v for v, _ in MODEL_CHOICES}
# Account picker: '' falls back to the agent host's own default.
ACCOUNT_MODEL_CHOICES = [("", "Host default")] + _MODEL_TIERS


@app.get("/account", response_class=HTMLResponse)
def account_form(request: Request, user=Depends(current_user)):
    return page(request, "account.html", user=user,
                models=ACCOUNT_MODEL_CHOICES, saved=False)


@app.post("/account", response_class=HTMLResponse)
def account_save(request: Request, default_model: str = Form(""),
                 user=Depends(current_user)):
    if default_model not in _MODEL_VALUES:
        default_model = ""
    conn = connect()
    try:
        conn.execute("UPDATE users SET default_model=? WHERE id=?",
                     (default_model, user["id"]))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    finally:
        conn.close()
    return page(request, "account.html", user=user,
                models=ACCOUNT_MODEL_CHOICES, saved=True)


@app.get("/workflows", response_class=HTMLResponse)
def list_workflows(request: Request, user=Depends(current_user)):
    conn = connect()
    try:
        if user["role"] == "admin":
            rows = conn.execute(
                "SELECT w.*, u.name owner_name FROM workflows w JOIN users u ON u.id=w.owner_id "
                "ORDER BY w.id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT w.*, u.name owner_name FROM workflows w JOIN users u ON u.id=w.owner_id "
                "WHERE w.owner_id=? OR w.id IN ("
                "  SELECT ws.workflow_id FROM workflow_sets ws "
                "  JOIN user_sets us ON us.set_id=ws.set_id WHERE us.user_id=?) "
                "ORDER BY w.id DESC", (user["id"], user["id"])).fetchall()
        wf_sets = {w["id"]: _set_names(conn, _workflow_set_ids(conn, w["id"])) for w in rows}
    finally:
        conn.close()
    return page(request, "workflows.html", user=user, workflows=rows, wf_sets=wf_sets)


@app.get("/workflows/new", response_class=HTMLResponse)
def new_workflow(request: Request, user=Depends(require_role("editor"))):
    conn = connect()
    try:
        sets = [r for r in conn.execute("SELECT * FROM sets ORDER BY name")
                if r["id"] in _assignable_set_ids(conn, user)]
        tool_names = [r["name"] for r in conn.execute(
            "SELECT name FROM mcp_tools WHERE enabled=1 ORDER BY name")]
    finally:
        conn.close()
    return page(request, "workflow_edit.html", user=user, wf=None, sets=sets, wf_set_ids=set(),
                models=MODEL_CHOICES, tool_names=tool_names)


def _validate_tools(tools: str):
    """Reject bad Tools JSON at save time. Mirrors agent runner.tools_config shape:
    a list of shortcut-name strings or inline MCP dicts {name, type, url, ...}."""
    try:
        items = json.loads(tools or "[]")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Tools must be valid JSON: {e}")
    if not isinstance(items, list):
        raise HTTPException(400, "Tools must be a JSON list.")
    for it in items:
        if isinstance(it, str):
            continue
        if not isinstance(it, dict):
            raise HTTPException(400, "Each tool must be a name string or a server object.")
        if not it.get("name"):
            raise HTTPException(400, 'Inline tool server needs a "name".')
        if it.get("type") in ("http", "sse") and not it.get("url"):
            raise HTTPException(400, f'Tool "{it["name"]}" ({it["type"]}) needs a "url".')


def _validate_inputs_spec(spec: str):
    """Reject bad Inputs spec JSON at save time: a list of {key, label, type} dicts,
    type one of text|textarea|file. key is required (it's the form/inputs name)."""
    try:
        items = json.loads(spec or "[]")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Inputs spec must be valid JSON: {e}")
    if not isinstance(items, list):
        raise HTTPException(400, "Inputs spec must be a JSON list.")
    keys = set()
    for it in items:
        if not isinstance(it, dict):
            raise HTTPException(400, "Each input must be an object with key/label/type.")
        key = it.get("key")
        if not key or not isinstance(key, str):
            raise HTTPException(400, 'Each input needs a non-empty "key".')
        if key in keys:
            raise HTTPException(400, f'Duplicate input key "{key}".')
        keys.add(key)
        if it.get("type", "text") not in ("text", "textarea", "file"):
            raise HTTPException(400, f'Input "{key}" type must be text, textarea, or file.')


@app.post("/workflows")
def create_workflow(request: Request, name: str = Form(...), sets: list[int] = Form([]),
                    inputs_spec: str = Form("[]"), action_prompt: str = Form(""),
                    eval_prompt: str = Form(""), tools: str = Form("[]"), model: str = Form(""),
                    user=Depends(require_role("editor"))):
    if model not in _MODEL_VALUES:
        model = ""
    _validate_tools(tools)
    _validate_inputs_spec(inputs_spec)
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO workflows (owner_id,name,inputs_spec,action_prompt,eval_prompt,tools,model)"
            " VALUES (?,?,?,?,?,?,?)",
            (user["id"], name, inputs_spec, action_prompt, eval_prompt, tools, model))
        _save_workflow_sets(conn, cur.lastrowid, set(sets), user)
        conn.commit()
        return redirect(request, f"/workflows/{cur.lastrowid}")
    finally:
        conn.close()


def _shares_set(conn, wid, user) -> bool:
    return conn.execute(
        "SELECT 1 FROM workflow_sets ws JOIN user_sets us ON us.set_id=ws.set_id "
        "WHERE ws.workflow_id=? AND us.user_id=? LIMIT 1", (wid, user["id"])).fetchone() is not None


def _get_workflow(conn, wid, user, *, for_edit=False):
    w = conn.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
    if w is None:
        raise HTTPException(404, "Workflow not found")
    owner_or_admin = w["owner_id"] == user["id"] or user["role"] == "admin"
    if for_edit and not owner_or_admin:
        raise HTTPException(403, "Not your workflow")
    if not for_edit and not (owner_or_admin or _shares_set(conn, wid, user)):
        raise HTTPException(403, "Not shared with you")
    return w


@app.get("/workflows/{wid}", response_class=HTMLResponse)
def view_workflow(request: Request, wid: int, user=Depends(current_user)):
    conn = connect()
    try:
        w = _get_workflow(conn, wid, user)
        set_names = _set_names(conn, _workflow_set_ids(conn, wid))
        runs = conn.execute(
            "SELECT * FROM runs WHERE workflow_id=? AND user_id=? ORDER BY id DESC LIMIT 20",
            (wid, user["id"])).fetchall()
    finally:
        conn.close()
    return page(request, "workflow_view.html", user=user, wf=w,
                inputs=json.loads(w["inputs_spec"] or "[]"), runs=runs, set_names=set_names)


@app.get("/workflows/{wid}/edit", response_class=HTMLResponse)
def edit_workflow(request: Request, wid: int, user=Depends(require_role("editor"))):
    conn = connect()
    try:
        w = _get_workflow(conn, wid, user, for_edit=True)
        wf_set_ids = _workflow_set_ids(conn, wid)
        # show the user's assignable sets, plus any already-on sets they can't manage (read-only)
        assignable = _assignable_set_ids(conn, user)
        sets = [r for r in conn.execute("SELECT * FROM sets ORDER BY name")
                if r["id"] in assignable or r["id"] in wf_set_ids]
        tool_names = [r["name"] for r in conn.execute(
            "SELECT name FROM mcp_tools WHERE enabled=1 ORDER BY name")]
    finally:
        conn.close()
    return page(request, "workflow_edit.html", user=user, wf=w, sets=sets, wf_set_ids=wf_set_ids,
                models=MODEL_CHOICES, tool_names=tool_names)


@app.post("/workflows/{wid}")
def update_workflow(request: Request, wid: int, name: str = Form(...),
                    sets: list[int] = Form([]), inputs_spec: str = Form("[]"),
                    action_prompt: str = Form(""), eval_prompt: str = Form(""),
                    tools: str = Form("[]"), model: str = Form(""),
                    user=Depends(require_role("editor"))):
    if model not in _MODEL_VALUES:
        model = ""
    _validate_tools(tools)
    _validate_inputs_spec(inputs_spec)
    conn = connect()
    try:
        _get_workflow(conn, wid, user, for_edit=True)
        conn.execute(
            "UPDATE workflows SET name=?,inputs_spec=?,action_prompt=?,eval_prompt=?,tools=?,model=? WHERE id=?",
            (name, inputs_spec, action_prompt, eval_prompt, tools, model, wid))
        _save_workflow_sets(conn, wid, set(sets), user)
        conn.commit()
        return redirect(request, f"/workflows/{wid}")
    finally:
        conn.close()


# ---------- runs ----------

@app.get("/runs", response_class=HTMLResponse)
def list_runs(request: Request, user=Depends(current_user)):
    """List runs. Regular users see only their own; admins see everyone's."""
    is_admin = user["role"] == "admin"
    conn = connect()
    try:
        sql = ("SELECT r.id, r.status, r.created_at, w.name AS wf, u.email AS who "
               "FROM runs r JOIN workflows w ON w.id=r.workflow_id "
               "JOIN users u ON u.id=r.user_id ")
        params = ()
        if not is_admin:
            sql += "WHERE r.user_id=? "
            params = (user["id"],)
        sql += "ORDER BY r.id DESC LIMIT 100"
        runs = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return page(request, "runs.html", user=user, runs=runs, is_admin=is_admin)


@app.post("/workflows/{wid}/run")
async def start_run(request: Request, wid: int, user=Depends(current_user)):
    form = await request.form()
    conn = connect()
    try:
        w = _get_workflow(conn, wid, user)
        spec = json.loads(w["inputs_spec"] or "[]")
        inputs = {}
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, user_id, inputs) VALUES (?,?,?)",
            (wid, user["id"], "{}"))
        run_id = cur.lastrowid
        run_dir = UPLOADS / str(run_id)
        for field in spec:
            key = field.get("key")
            if field.get("type") == "file":
                f = form.get(key)
                if isinstance(f, UploadFile):
                    run_dir.mkdir(parents=True, exist_ok=True)
                    dest = run_dir / Path(f.filename).name
                    dest.write_bytes(await f.read())
                    inputs[key] = {"file": dest.name}
            else:
                inputs[key] = form.get(key)
        conn.execute("UPDATE runs SET inputs=? WHERE id=?", (json.dumps(inputs), run_id))
        conn.commit()
        return redirect(request, f"/runs/{run_id}")
    finally:
        conn.close()


def _owned_run(conn, rid, user):
    """Fetch a run the user may see (own, or admin), else 404."""
    r = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if r is None or (r["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Run not found")
    return r


def _run_logs(conn, rid):
    return [dict(row) for row in conn.execute(
        "SELECT ts, message FROM run_logs WHERE run_id=? ORDER BY id", (rid,)).fetchall()]


@app.get("/runs/{rid}", response_class=HTMLResponse)
def view_run(request: Request, rid: int, user=Depends(current_user)):
    conn = connect()
    try:
        r = _owned_run(conn, rid, user)
        logs = _run_logs(conn, rid)
    finally:
        conn.close()
    result_html = (_MD_STYLE + md.markdown(r["result"], extensions=["fenced_code", "tables"])
                   if r["result"] else None)
    return page(request, "run.html", user=user, run=r, logs=logs, result_html=result_html)


@app.get("/runs/{rid}/status")
def run_status(rid: int, user=Depends(current_user)):
    conn = connect()
    try:
        r = _owned_run(conn, rid, user)
        logs = _run_logs(conn, rid)
    finally:
        conn.close()
    return {"status": r["status"], "result": r["result"], "error": r["error"], "logs": logs}


def _agent_files(method: str, path: str, **kw):
    """Proxy to the agent host's sandbox file API with the service token."""
    if not AGENT_FILES_URL:
        raise HTTPException(503, "Sandbox browsing not configured (AGENT_FILES_URL unset)")
    return httpx.request(method, f"{AGENT_FILES_URL}{path}",
                         headers={"Authorization": f"Bearer {AGENT_TOKEN}"}, timeout=30, **kw)


@app.get("/runs/{rid}/files", response_class=HTMLResponse)
def run_files(request: Request, rid: int, user=Depends(current_user)):
    conn = connect()
    try:
        _owned_run(conn, rid, user)   # authorize before proxying
    finally:
        conn.close()
    files, error = [], None
    try:
        resp = _agent_files("GET", f"/sandbox/{rid}")
        if resp.status_code == 200:
            files = resp.json()["files"]
        elif resp.status_code == 404:
            error = "No sandbox on the agent host (run hasn't started, or files were cleaned)."
        else:
            error = f"Agent host returned {resp.status_code}."
    except HTTPException:
        raise
    except Exception as e:
        error = f"Cannot reach agent host: {e}"
    return page(request, "run_files.html", user=user, rid=rid, files=files, error=error)


@app.get("/runs/{rid}/files/{path:path}")
def run_file(request: Request, rid: int, path: str, dl: int = 0, user=Depends(current_user)):
    conn = connect()
    try:
        _owned_run(conn, rid, user)
    finally:
        conn.close()
    resp = _agent_files("GET", f"/sandbox/{rid}/file", params={"path": path, "dl": dl})
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "File not available")
    headers = {}
    if "content-disposition" in resp.headers:
        headers["content-disposition"] = resp.headers["content-disposition"]
    media = resp.headers.get("content-type", "application/octet-stream")
    # Inline view: only raster images keep their rich type; force everything else (text/html,
    # svg, ...) to text/plain so untrusted content can't execute scripts in our origin.
    # Downloads (dl=1) keep the real type but go as an attachment, so the browser won't render them.
    is_raster = media.startswith("image/") and "svg" not in media
    if not dl and not is_raster:
        media = "text/plain; charset=utf-8"
    return Response(content=resp.content, media_type=media, headers=headers)


IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif"}  # raster only; svg shown as code
# extensions we always treat as inline text/code (others fall back to a utf-8 decode test)
TEXT_EXT = {"txt", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "cfg",
            "conf", "env", "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "scss", "sh",
            "bash", "zsh", "sql", "c", "h", "cpp", "hpp", "cc", "go", "rs", "java", "rb", "php",
            "pl", "lua", "r", "kt", "swift", "dart", "vue", "svelte", "rst", "tex", "svg"}
TEXT_NAMES = {"dockerfile", "makefile", ".gitignore", ".env", "license", "readme"}
MAX_INLINE = 1_000_000  # render up to ~1MB inline; bigger -> download only

# Styling injected into the sandboxed markdown iframe (no app CSS reaches inside).
# Light by default; the parent sets <html data-theme=dark> on the iframe to match the app toggle.
_MD_STYLE = (
    "<style>"
    "body{font-family:system-ui,sans-serif;padding:1rem;max-width:46rem;margin:auto;line-height:1.5;"
    "color:#222;background:#fff}"
    "img{max-width:100%}"
    "pre{background:#f5f5f5;padding:.6rem;overflow:auto;border-radius:6px}"
    "code{background:#f0f0f0;padding:.1rem .3rem;border-radius:4px}"
    "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3rem}"
    "a{color:#6200EE}"
    "html[data-theme=dark] body{color:#e6e6e6;background:#1a1a1a}"
    "html[data-theme=dark] pre,html[data-theme=dark] code{background:#2a2a2a}"
    "html[data-theme=dark] td,html[data-theme=dark] th{border-color:#444}"
    "html[data-theme=dark] a{color:#bb86fc}"
    "</style>")


# Pygments token CSS: light by default, dark under [data-theme=dark] (more specific wins).
_HL = HtmlFormatter(cssclass="hl")
CODE_CSS = (HtmlFormatter(style="default").get_style_defs(".hl")
            + HtmlFormatter(style="monokai").get_style_defs("[data-theme=dark] .hl")
            + " .hl pre{padding:.8rem;border-radius:6px;overflow:auto;margin:0}")


def _highlight(name: str, text: str) -> str:
    """Syntax-highlight text to HTML; lexer by filename, then content guess, then plain."""
    try:
        lexer = get_lexer_for_filename(name, stripall=False)
    except ClassNotFound:
        try:
            lexer = guess_lexer(text)
        except ClassNotFound:
            lexer = TextLexer()
    return highlight(text, lexer, _HL)


def _classify(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in IMAGE_EXT:
        return "image"
    if ext in {"md", "markdown"}:
        return "markdown"
    if ext in TEXT_EXT or name in TEXT_NAMES:
        return "text"
    return "maybe"   # decide by trying to decode as utf-8


@app.get("/runs/{rid}/view/{path:path}", response_class=HTMLResponse)
def view_file(request: Request, rid: int, path: str, render: int = 0, user=Depends(current_user)):
    conn = connect()
    try:
        _owned_run(conn, rid, user)
    finally:
        conn.close()
    kind = _classify(path)
    is_html = path.rsplit(".", 1)[-1].lower() in ("html", "htm")
    ctx = dict(user=user, rid=rid, path=path, fname=path.rsplit("/", 1)[-1], is_html=is_html)

    if kind == "image":  # no need to fetch bytes; the <img> hits the raw passthrough
        return page(request, "file_view.html", kind="image", **ctx)

    resp = _agent_files("GET", f"/sandbox/{rid}/file", params={"path": path, "dl": 0})
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "File not available")
    data = resp.content
    if len(data) > MAX_INLINE:
        return page(request, "file_view.html", kind="toolarge", **ctx)

    if kind == "maybe":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return page(request, "file_view.html", kind="binary", **ctx)
    else:
        text = data.decode("utf-8", "replace")

    if is_html and render:
        # render the file's HTML in a sandboxed iframe (scripts disabled) -> safe
        return page(request, "file_view.html", kind="htmlrender", body=text, **ctx)
    if kind == "markdown":
        html = _MD_STYLE + md.markdown(text, extensions=["fenced_code", "tables"])
        # rendered in a sandboxed iframe (scripts disabled) -> untrusted HTML can't run JS
        return page(request, "file_view.html", kind="markdown", body=html, **ctx)
    # everything else textual -> syntax-highlighted (plain text falls back to no-color)
    return page(request, "file_view.html", kind="code",
                body=_highlight(ctx["fname"], text), code_css=CODE_CSS, **ctx)


# ---------- agent-facing API (service token) ----------

def _check_agent(authorization: str | None):
    if not AGENT_TOKEN or authorization != f"Bearer {AGENT_TOKEN}":
        raise HTTPException(401, "Bad agent token")


@app.post("/api/runs/reap-running")
def reap_running_runs(authorization: str | None = Header(None)):
    """Agent calls this on startup. With one worker, any run still 'running' is orphaned
    (the worker that claimed it died), so mark it errored instead of leaving it stuck forever.
    ponytail: single-worker assumption — would need worker-scoping if we ever run several."""
    _check_agent(authorization)
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE runs SET status='error', error='interrupted (agent restarted)', "
            "updated_at=datetime('now') WHERE status='running'")
        conn.commit()
        return {"reaped": cur.rowcount}
    finally:
        conn.close()


def _resolve_tools(conn, run_id: int, tools_json: str) -> str:
    """Turn shortcut names in a workflow's tools list into inline MCP server dicts,
    using the admin-managed mcp_tools registry. Disabled tools are dropped with a
    run-log note; unknown names pass through (the agent may still know them via its
    own fallback dict). Inline dicts pass through untouched."""
    try:
        items = json.loads(tools_json or "[]")
    except json.JSONDecodeError:
        return tools_json
    registry = {t["name"]: t for t in conn.execute("SELECT * FROM mcp_tools")}
    out = []
    for it in items:
        t = registry.get(it) if isinstance(it, str) else None
        if t is None:
            out.append(it)                      # inline dict, or unknown shortcut
        elif t["enabled"]:
            out.append({"name": t["name"], **json.loads(t["config"])})
        else:
            conn.execute("INSERT INTO run_logs (run_id, ts, message) VALUES (?, datetime('now'), ?)",
                         (run_id, f"tool '{it}' is disabled — skipped"))
    return json.dumps(out)


@app.post("/api/runs/next")
def claim_next_run(authorization: str | None = Header(None)):
    """Atomically claim the oldest pending run. Returns it + its workflow def, or 204."""
    _check_agent(authorization)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")  # ponytail: single writer; serialize the claim
        r = conn.execute(
            "SELECT * FROM runs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if r is None:
            conn.rollback()
            return Response(status_code=204)
        conn.execute("UPDATE runs SET status='running', updated_at=datetime('now') WHERE id=?",
                     (r["id"],))
        w = dict(conn.execute("SELECT * FROM workflows WHERE id=?", (r["workflow_id"],)).fetchone())
        if not w["model"]:  # "Account default" -> the run-starter's default ('' = host default)
            owner = conn.execute("SELECT default_model FROM users WHERE id=?",
                                 (r["user_id"],)).fetchone()
            w["model"] = owner["default_model"] if owner else ""
        w["tools"] = _resolve_tools(conn, r["id"], w["tools"])
        conn.commit()
        return {"run": dict(r) | {"status": "running"}, "workflow": w}
    finally:
        conn.close()


@app.get("/api/workflows/resolve")
def resolve_workflow(name: str, run_id: int, authorization: str | None = Header(None)):
    """Agent resolves the next workflow in a chain by name, scoped to the run's user's
    visibility (owner / admin / shares a set). 404 none, 409 ambiguous."""
    _check_agent(authorization)
    conn = connect()
    try:
        run = conn.execute("SELECT user_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, "Run not found")
        usr = conn.execute("SELECT id, role FROM users WHERE id=?", (run["user_id"],)).fetchone()
        user = {"id": usr["id"], "role": usr["role"]}
        rows = conn.execute("SELECT * FROM workflows WHERE name=?", (name,)).fetchall()
        visible = [w for w in rows
                   if w["owner_id"] == user["id"] or user["role"] == "admin"
                   or _shares_set(conn, w["id"], user)]
        if not visible:
            raise HTTPException(404, f"No accessible workflow named {name!r}")
        if len(visible) > 1:
            raise HTTPException(409, f"Workflow name {name!r} is ambiguous")
        w = dict(visible[0])
        if not w["model"]:  # mirror claim_next_run: '' -> run-user default -> host default
            owner = conn.execute("SELECT default_model FROM users WHERE id=?",
                                 (user["id"],)).fetchone()
            w["model"] = owner["default_model"] if owner else ""
        w["tools"] = _resolve_tools(conn, run_id, w["tools"])
        conn.commit()   # persist any 'tool disabled' run-log notes
        return w
    finally:
        conn.close()


@app.get("/api/runs/{rid}/files/{name}")
def get_run_file(rid: int, name: str, authorization: str | None = Header(None)):
    """Agent downloads an uploaded input file into its sandbox."""
    _check_agent(authorization)
    path = UPLOADS / str(rid) / Path(name).name  # Path(name).name strips any traversal
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


@app.post("/api/runs/{rid}/log")
def append_run_log(rid: int, payload: dict, authorization: str | None = Header(None)):
    """Agent posts a progress message shown live on the run page."""
    _check_agent(authorization)
    msg = (payload.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    conn = connect()
    try:
        conn.execute("INSERT INTO run_logs (run_id, message) VALUES (?,?)", (rid, msg[:2000]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/runs/{rid}")
def update_run(rid: int, payload: dict, authorization: str | None = Header(None)):
    """Agent reports progress/result. Accepts status, result, error, sandbox_path."""
    _check_agent(authorization)
    fields = {k: payload[k] for k in ("status", "result", "error", "sandbox_path") if k in payload}
    if not fields:
        raise HTTPException(400, "Nothing to update")
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
    conn = connect()
    try:
        conn.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), rid))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
