-- 003_sets: named sets group users and workflows; replaces workflow.visibility.
-- A workflow is visible to a user if they own it, are admin, or share a set with it.
-- (workflows.visibility column is retired/unused; left in place — SQLite DROP COLUMN isn't portable.)
CREATE TABLE sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE user_sets (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    set_id  INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, set_id)
);

CREATE TABLE workflow_sets (
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    set_id      INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    PRIMARY KEY (workflow_id, set_id)
);

-- sets a new account joins when it signs up with an invite (0, 1, or more)
CREATE TABLE invite_sets (
    invite_id INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
    set_id    INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    PRIMARY KEY (invite_id, set_id)
);
