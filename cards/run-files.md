# run-files

Browsing a run's sandbox files from the web UI — a live passthrough, no copying.

The files a run produces live only in the agent's sandbox on `home` (`sandboxes/<run_id>/`). The
web UI is on a different host (`rpi6`) and has no shared filesystem. Instead of pushing files back,
the web server **proxies** browse/view/download to a small read-only file API on the agent host.

**Agent side** (`agent/files_api.py`, run in-process by `worker.py` on `:9006`, exposed via home's
Apache at `/workflow-agent`):
- `GET /sandbox/{run_id}` → `{files: [{path, size}]}` (recursive `rglob`).
- `GET /sandbox/{run_id}/file?path=…&dl=0|1` → file bytes, inline or `attachment`.
- Bearer-token auth (the shared `AGENT_TOKEN`); path-traversal guarded (target must stay under the
  run's sandbox dir).

**Web side** (`web/app.py`, `AGENT_FILES_URL` = e.g. `https://home.memention.net/workflow-agent`):
- `GET /runs/{id}/files` → `_owned_run` authorizes (owner or admin) **then** proxies the listing,
  renders `run_files.html`.
- `GET /runs/{id}/files/{path:path}?dl=` → authorizes, proxies the bytes back with the upstream
  content-type / content-disposition.
- If `AGENT_FILES_URL` is unset or the agent host is unreachable, the files page shows a notice
  rather than erroring the run view.

The run page links to it ("Browse files"). Authorization is enforced on the web side; the agent API
trusts the token. Files appear as soon as the agent creates them (live), not only after completion.

**Inline viewer** (`GET /runs/{id}/view/{path}`, `file_view.html`): renders by extension — images as
`<img>` (no fetch, points at the raw passthrough; raster only — svg is shown as code), text/code
**syntax-highlighted server-side with Pygments** (lexer by filename → content guess → plain; light +
dark token CSS scoped by `[data-theme]`), and **markdown rendered
server-side** (python-`markdown`) shown inside a `<iframe sandbox="allow-same-origin" srcdoc>`. The
sandbox **without `allow-scripts`** is the XSS defense — sandbox file content is untrusted and an
admin may view another user's files, so rendered HTML must not run JS; `allow-same-origin` only lets
the parent mirror the app's dark/light theme into the iframe (the same pattern renders a run's
markdown `result` on the run page). Unknown extensions fall back to a
utf-8 decode test (text vs binary); files over ~1MB and binaries offer download only.

**HTML files** show as code by default with an extra **Render** button (`?render=1`) that displays the
page in a `sandbox`ed iframe (scripts off) — and back via "view as code". The **raw** passthrough
(`/runs/{id}/files/{path}`) forces non-raster-image content to `text/plain` on inline view (`dl=0`),
so untrusted HTML/SVG can't execute scripts in our origin; downloads (`dl=1`) keep the real type as
an attachment. The agent client (`agent/client.py`) retries transient transport errors so a web
restart mid-run doesn't fail the final report.
