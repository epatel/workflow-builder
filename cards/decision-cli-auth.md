# decision-cli-auth

The agent authenticates via the Claude Code CLI login, not an API key.

**Choice:** The Agent SDK on the agent host uses the Claude Code CLI's own credentials
(the user's subscription login), and `ANTHROPIC_API_KEY` is left unset everywhere.

**Why:** Use the existing Claude subscription through the CLI session rather than a separately
billed API key.

**Consequences:**
- The agent host (`home`) must have the Claude Code CLI installed and logged in
  (`claude` / `claude setup-token`), plus Node present.
- Do not add `ANTHROPIC_API_KEY` to env, `.env`, or the Makefile — if set, it overrides CLI auth.
- The local self-checks don't exercise a real SDK call (that needs the logged-in host); a real
  end-to-end run is a deploy-host verification step.
- **Binary gotcha:** the working CLI on `home` is `~/.local/bin/claude`; the npm-global
  `/usr/local/bin/claude` can be a broken stub ("native binary not installed") that fails with
  `Exec format error`. `agent/run.sh` puts `~/.local/bin` first on PATH so the SDK finds the good one.
