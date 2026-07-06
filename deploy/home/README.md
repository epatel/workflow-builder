# Agent host (`home`) — no Apache config needed

The original idea pencilled in `https://home.memention.net/workflow-agent/`, but under the
final architecture the **agent is a polling worker with no inbound HTTP server** — it reaches
*out* to the web server's API and to the puppeteer MCP on the LAN. Nothing listens for inbound
requests, so there is no port to reverse-proxy and no `endpoints.d` / vhost entry to add.

If you ever want a health endpoint, add one to `worker.py` and proxy it then.

## Running the worker on `home`

Prereqs: Node + the Claude Code CLI installed and logged in (`claude` / `claude setup-token`).
Auth is the CLI's — do **not** set `ANTHROPIC_API_KEY`.

```sh
cd agent
cp .env.example .env     # set WEB_URL=https://rpi6.memention.net/workflow and a matching AGENT_TOKEN
./run.sh
```

Keep it alive with whatever the host already uses (a `screen`/`tmux` session, a systemd unit,
or `nohup`). One worker handles one run at a time; start more `run.sh` processes for more.
