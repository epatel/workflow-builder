.PHONY: venv web migrate agent-venv agent test deploy deploy-web deploy-agent clean
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
AVENV := agent/.venv
WEB_HOST := rpi6
AGENT_HOST := home
RSYNC := rsync -az --delete --exclude .venv --exclude venv --exclude __pycache__ --exclude .env

$(VENV):
	python3 -m venv $(VENV)

venv: $(VENV)
	$(PIP) install -q -r web/requirements.txt

migrate: venv
	cd web && ../$(PY) db.py

# Run the web server (accounts + workflow UI). Reads web/.env.
web: venv
	cd web && ../$(VENV)/bin/uvicorn app:app --reload --host 0.0.0.0 --port 8000

$(AVENV):
	python3 -m venv $(AVENV)

agent-venv: $(AVENV)
	$(AVENV)/bin/pip install -q -r agent/requirements.txt

# Run the agent worker. Needs the Claude Code CLI installed & logged in. Reads agent/.env.
agent: agent-venv
	cd agent && ./.venv/bin/python worker.py

test: venv agent-venv
	cd web && ../$(VENV)/bin/python -m pip install -q httpx && ../$(VENV)/bin/python test_smoke.py && ../$(VENV)/bin/python test_logic.py
	cd agent && ./.venv/bin/python test_agent.py && ./.venv/bin/python test_runner.py

# Deploy both services (web to rpi6, agent to home).
deploy: deploy-web deploy-agent

# Deploy web to rpi6: sync code, install the Apache endpoint, reload Apache.
# Preserves the host's .env and workflow.db (rsync excludes .env / *.db).
deploy-web:
	$(RSYNC) --exclude '*.db' --exclude uploads web/ $(WEB_HOST):workflow-web/
	scp deploy/rpi6/workflow.conf $(WEB_HOST):/tmp/workflow.conf
	ssh $(WEB_HOST) 'sudo mv /tmp/workflow.conf /etc/apache2/endpoints.d/workflow.conf && sudo systemctl reload apache2'
	@echo "On $(WEB_HOST): cd workflow-web && ./run.sh   (keep alive via systemd/screen/tmux)"

# Deploy agent to home: sync code only (no Apache — worker has no inbound server).
deploy-agent:
	$(RSYNC) --exclude sandboxes agent/ $(AGENT_HOST):workflow-agent/
	@echo "On $(AGENT_HOST): cd workflow-agent && ./run.sh   (needs Claude Code CLI logged in)"

clean:
	rm -rf $(VENV) $(AVENV) web/__pycache__ web/workflow.db web/uploads agent/__pycache__ agent/sandboxes
