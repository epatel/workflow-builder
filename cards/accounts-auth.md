# accounts-auth

Signup, login, roles, invites, email verification, password reset. Lives in `web/app.py` + `web/auth.py`.

**Roles are an int ladder**, not a permissions matrix: `user(0) ⊂ editor(1) ⊂ admin(2)`
(`auth.ROLE_RANK`). Gate routes with `require_role("editor")` etc.; it 403s if rank is too low.

**First-signup bootstrap:** the very first account (when `users` is empty) needs no invite code,
becomes **admin**, and is pre-verified. Every signup after that requires a valid unused invite
code, and inherits the role the invite was created with.

**Invites:** admins create them (`POST /admin/invites`, role-typed). One code, one use — consumed
by setting `invites.used_by` at signup. An invite may also list **sets** (`invite_sets`); the new
account joins all of them on signup.

**Sets (visibility):** named groups created by admins (`sets`); they replace the old public/private
flag. Users belong to sets (`user_sets`, managed by admin via `POST /admin/users/{id}/sets`);
workflows belong to sets (`workflow_sets`). A user may **see/run** a workflow if they own it, are
admin, or share a set with it (`_shares_set`). Membership is unified: being in a set also lets an
editor put their own workflows in it. **0 sets = private** (owner + admin only). An editor can only
assign sets they belong to; `_save_workflow_sets` preserves admin-assigned sets the editor can't see.
Admins create/delete sets (`POST /admin/sets`, `/admin/sets/{id}/delete`). (Migration 003;
`workflows.visibility` is retired/unused.)

**Sessions:** JWT in an httpOnly `session` cookie (`auth.make_session` / `current_user`).
Blocked or deleted users fail the `current_user` lookup, so a stale cookie stops working.

**Email flows:** `tokens` table holds both `verify` and `reset` tokens (kind-discriminated,
`secrets.token_urlsafe`, 24h expiry, single-use). Verification is required before a non-first
user can log in. Password reset and "forgot" deliberately don't reveal whether an email exists.
Mail goes through Mailjet; when Mailjet env is unset, links are printed to the console (dev).

**Account page:** every logged-in user has `GET/POST /account` (linked from the nav name).
Currently it sets a **default model** (`users.default_model`, migration 005). A workflow whose
`model` is `''` ("Account default") resolves to the *run-starter's* `default_model` when the run is
claimed (`claim_next_run`); if that's also `''`, the agent falls back to the host default. The
account picker's empty option is labelled "Host default". Choices share `_MODEL_TIERS` with the
workflow picker; an unknown value is coerced to `''`.

**Admin account actions:** block / unblock / delete other users (`POST /admin/users/{id}/{action}`).
You cannot act on your own account through this route.

**Admin run cleanup:** the admin page lists recent runs with a per-run delete
(`POST /admin/runs/{id}/delete`) and a purge-by-age form (`POST /admin/runs/purge`, `days`). Both go
through `_delete_run`, which removes the sandbox on the agent host (DELETE `/sandbox/{id}`, best-effort),
the uploaded inputs, and the DB row (logs cascade).
