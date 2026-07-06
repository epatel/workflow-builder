-- 004_workflow_model: per-workflow model override ('' = agent-host default)
ALTER TABLE workflows ADD COLUMN model TEXT NOT NULL DEFAULT '';
