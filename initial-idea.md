Dynamic workflow builder + runner
---------------------------------

Workflow using Claude Code Agent SDK for agent work

Full account feature. Sign-up, Login, Confirm email, Reset password. Allow initial sign-up be without an invite code and let inital account become Admin account. Admin can create invite codes

Account types are, Admin, Editor, User

User can select a workflow. Enter/upload needed data. Start workflow. Observe process and view results

Editor can do everything User can do + Create workflows, also edit their own workflows

Admin can do everything Editors can do + Create invite codes, block accounts, delete accounts

Use python and use a local venv

2 Parts, 1) Front end server with account managment, 2) Agent server who does the work

Use MAILJET for email services (same pattern as an earlier internal project).

Create a Makefile for actions

A running workflow should have its own sandbox folder for files and data

A workflow definition state inputs, files or data. A prompt for actions. A prompt for evaluating or summarize a result. Also some settings like if it is public or private. 

Add tools for the workflow agent, ie puppeteer for browsing

---

Demo deployment plan

Front-end: https://rpi6.memention.net/workflow/
Control server with "ssh rpi6". Apache2 is running on it. See /etc/apache2/endpoints.d/ for serving ports to /workflow

Agent-host: https://home.memention.net/workflow-agent/
Control server with "ssh home". Also using Apache2 but serving ports configured in /etc/apache2/sites-enabled/000-default-le-ssl.conf

Puppeteer-mcp: 
  "puppeteer": {
    "type": "http",
    "url": "http://<lan-host>:8765/mcp"
  },
