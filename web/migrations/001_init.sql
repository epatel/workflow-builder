-- 001_init: accounts, tokens, invites, workflows, runs
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    pw_hash       TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','editor','user')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked')),
    email_verified INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- email verification + password reset tokens (kind discriminates)
CREATE TABLE tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('verify','reset')),
    token      TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    role       TEXT NOT NULL CHECK (role IN ('admin','editor','user')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE workflows (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    visibility   TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('public','private')),
    inputs_spec  TEXT NOT NULL DEFAULT '[]',   -- JSON: [{key,label,type}]
    action_prompt TEXT NOT NULL DEFAULT '',
    eval_prompt  TEXT NOT NULL DEFAULT '',
    tools        TEXT NOT NULL DEFAULT '[]',   -- JSON: ["puppeteer", ...]
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id  INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','error')),
    inputs       TEXT NOT NULL DEFAULT '{}',    -- JSON
    result       TEXT,
    error        TEXT,
    sandbox_path TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_tokens_token ON tokens(token);
