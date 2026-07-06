"""Agent worker: poll the web queue, run each workflow, report back.

ponytail: one worker, one job at a time. Run more processes if throughput matters.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn

from client import WebClient
import runner
import files_api

WEB_URL = os.getenv("WEB_URL", "http://localhost:8000")
AGENT_TOKEN = os.environ["AGENT_TOKEN"]  # required; matches the web server's token
SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", Path(__file__).parent / "sandboxes"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
HTTP_PORT = int(os.getenv("AGENT_HTTP_PORT", "9006"))  # sandbox file API (behind Apache)


async def handle(web: WebClient, job: dict):
    run, wf = job["run"], job["workflow"]
    sandbox = SANDBOX_ROOT / str(run["id"])
    sandbox.mkdir(parents=True, exist_ok=True)
    await web.report(run["id"], sandbox_path=str(sandbox))
    try:
        result = await runner.run_workflow(wf, run, sandbox, web)
        await web.report(run["id"], status="done", result=result)
        print(f"[run {run['id']}] done")
    except Exception as e:  # report failure, keep the worker alive
        await web.report(run["id"], status="error", error=str(e))
        print(f"[run {run['id']}] error: {e}")


async def poll_loop(web: WebClient):
    print(f"agent worker polling {WEB_URL} every {POLL_SECONDS}s")
    try:
        while True:
            try:
                job = await web.claim_next()
            except Exception as e:
                print(f"poll failed: {e}")
                job = None
            if job is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await handle(web, job)
    finally:
        await web.aclose()


async def main():
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    web = WebClient(WEB_URL, AGENT_TOKEN)
    try:  # any run still 'running' is orphaned by a previous worker — fail it, don't leave it stuck
        n = await web.reap_running()
        if n:
            print(f"reaped {n} orphaned running run(s) -> error")
    except Exception as e:
        print(f"startup reap failed: {e}")
    # Serve the sandbox file API and run the poll loop together in one process.
    server = uvicorn.Server(uvicorn.Config(
        files_api.app, host="127.0.0.1", port=HTTP_PORT, log_level="warning"))
    print(f"sandbox file API on 127.0.0.1:{HTTP_PORT}")
    await asyncio.gather(server.serve(), poll_loop(web))


if __name__ == "__main__":
    asyncio.run(main())
