# decision-sqlite-migrations

SQLite with a stdlib numbered-migration path; no ORM, no Alembic.

**Choice:** The web service stores everything in one SQLite file (`web/workflow.db`) accessed with
raw `sqlite3`. Schema changes ship as numbered SQL files in `web/migrations/`
(`001_init.sql`, `002_*.sql`, …), applied in order and tracked with `PRAGMA user_version`.

**Why:** Any DB creation in this project must include a migration path from the start, not a
one-shot schema. The lazy stdlib path satisfies that without an ORM/Alembic dependency.

**How it works:** `web/db.py`'s `migrate()` lists `NNN_*.sql`, skips any whose number `<=`
`user_version`, runs the rest in order, and bumps `user_version`. Each file runs as one transaction
(`BEGIN; <sql>; PRAGMA user_version = N; COMMIT;` in a single `executescript`, rolled back on
error), so a failing migration leaves the schema and version untouched — which also means a
migration file must not contain its own `BEGIN`/`COMMIT`. Idempotent; runs on web startup and via
`make migrate` (`python web/db.py`).

**To change the schema:** add the next-numbered file. Never edit an already-applied migration —
write a new one. Reach for a real migration tool only if this measurably falls short.
