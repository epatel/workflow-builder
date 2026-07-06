-- 006_mcp_tools: admin-editable registry of MCP tool shortcuts. A workflow's `tools`
-- list may name one of these; the web resolves the name to an inline server dict when
-- the agent claims the run. Disabled tools are skipped (with a run-log note). Seeded
-- with the previously hardcoded puppeteer entry from agent/runner.py.
CREATE TABLE mcp_tools (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    type    TEXT NOT NULL DEFAULT 'http',
    url     TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
-- Placeholder URL — point it at your own puppeteer MCP in Admin → Tools.
INSERT INTO mcp_tools (name, type, url, enabled)
VALUES ('puppeteer', 'http', 'http://localhost:8765/mcp', 1);
