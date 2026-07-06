-- 002_run_logs: progress messages the agent posts during a run
CREATE TABLE run_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    message TEXT NOT NULL
);
CREATE INDEX idx_run_logs_run ON run_logs(run_id);
