# decision-puppeteer-on-lan

The puppeteer MCP stays on the LAN; the agent uses the local SDK, no public tunnel.

**Choice:** Use the **local** Claude Agent SDK on the agent host and let it reach the puppeteer MCP
directly at its LAN address (e.g. `http://192.168.x.x:8765/mcp`). Do not expose puppeteer publicly,
and do not use the hosted/cloud Agents API.

**Why:** Two options were on the table — local SDK vs. the hosted Agents API. The hosted API runs
in Anthropic's cloud and could not reach a `192.168.*` MCP without tunnelling puppeteer to the
public internet, which adds a browser-automation attack surface just to avoid installing Node.
The agent host is already on the LAN, so the local SDK reaches puppeteer with zero extra exposure,
gives per-run sandbox folders for free, and matches the brief's "Claude Code Agent SDK" wording.

**Consequences:**
- The agent must run on a host inside the LAN that can reach the MCP IP.
- New MCP/browser tools are wired in `agent/runner.py`'s `MCP_TOOLS` dict, not via a public proxy.
