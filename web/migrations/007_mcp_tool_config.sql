-- 007_mcp_tool_config: replace the single url (+type) columns with a free-form JSON
-- `config` — the server dict minus "name" (type, url, headers, ...) — so tools that
-- need auth headers or other MCP options fit without schema changes. Existing rows
-- are converted to {"type": ..., "url": ...}.
CREATE TABLE mcp_tools_new (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    config  TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
INSERT INTO mcp_tools_new (id, name, config, enabled)
    SELECT id, name, json_object('type', type, 'url', url), enabled FROM mcp_tools;
DROP TABLE mcp_tools;
ALTER TABLE mcp_tools_new RENAME TO mcp_tools;
