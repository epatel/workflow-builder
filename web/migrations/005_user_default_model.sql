-- 005_user_default_model: per-account default model. A workflow whose model is ''
-- ("Account default") resolves to its run-starter's default_model at claim time;
-- '' here in turn means the agent-host default.
ALTER TABLE users ADD COLUMN default_model TEXT NOT NULL DEFAULT '';
