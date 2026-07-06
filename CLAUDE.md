# Workflow Builder

A browser tool for defining agent workflows and running them: a user picks a workflow, supplies
inputs/files, starts it, and watches the result. Two FastAPI services — a **web** front end
(accounts, workflow CRUD, the run queue, SQLite) and an **agent** worker that executes each run
with the Claude Agent SDK in its own sandbox folder. Python 3.12+; the agent host also needs Node
and a logged-in Claude Code CLI.

Read `@project-plan.md` first — it holds the shared goal, settled decisions, and current state.

## Deploy

Live at https://rpi6.memention.net/workflow. Both services run as detached `screen` sessions
(no systemd; they don't survive a host reboot). See the README **Deploy** section for the full
runbook; the short version:

```sh
make deploy-web      # rsync web/   -> rpi6:~/workflow-web  + install/reload Apache (strips /workflow -> :9005)
make deploy-agent    # rsync agent/ -> home:~/workflow-agent (worker + file API on :9006 via Apache /workflow-agent)
# then restart the affected screen, e.g. on rpi6:
ssh rpi6 'screen -S workflow -X quit; sleep 2; cd ~/workflow-web && screen -dmS workflow bash -c "./run.sh > run.log 2>&1"'
# agent screen is named "workflow-agent"; web migrations apply automatically on restart via run.sh.
```

`.env` files live on the hosts and are never synced (deploy excludes `.env`/`*.db`/`venv`/sandboxes).
`home` needs the Claude Code CLI logged in (no `ANTHROPIC_API_KEY`).

## Context cards

When you change how something works, update the relevant card(s) below and the README in the same change so they don't drift from the code.

Load a card when the situation matches its trigger.

- **cards/architecture.md** — anything about how the two services connect, the request/run flow end to end, or where things are deployed.
- **cards/accounts-auth.md** — touching signup, login, sessions, roles/permissions, invite codes, email verification, password reset, or **sets / workflow visibility / who-can-see-a-workflow**.
- **cards/run-queue.md** — touching how runs are created, claimed, or reported; the agent-facing `/api/runs/*` endpoints; or run status polling.
- **cards/run-files.md** — touching browsing/viewing/downloading a run's sandbox files, the agent's `/sandbox/*` file API, or the web→agent passthrough.
- **cards/agent-runner.md** — touching how the agent executes a workflow: the worker loop, sandboxes, tool/MCP wiring, or the action/eval passes.
- **cards/decision-cli-auth.md** — when anything involves the agent's API key or authentication, or `ANTHROPIC_API_KEY` comes up.
- **cards/decision-sqlite-migrations.md** — when changing the database schema or wondering how migrations work here.
- **cards/decision-puppeteer-on-lan.md** — when wiring browser/MCP tools, considering the hosted Agents API, or thinking about exposing puppeteer.
