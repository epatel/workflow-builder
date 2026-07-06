-- 008_endpoints: named API endpoints that start a workflow run via bearer token
CREATE TABLE endpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    name        TEXT NOT NULL UNIQUE,
    token       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
