-- 010_run_disk_bytes: total size of a run's sandbox, reported by the agent when the run
-- finishes. NULL = unknown (still running, reaped after an agent crash, or finished before this column).
ALTER TABLE runs ADD COLUMN disk_bytes INTEGER;
