"""Thin async client for the web server's agent API."""
import asyncio
import httpx


class WebClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=30)

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        """Request with a couple of retries on transient transport errors.

        A stale pooled connection (e.g. the web server restarted) raises a
        TransportError on the next call; retrying re-establishes it.
        """
        for attempt in range(3):
            try:
                return await self.http.request(method, f"{self.base}{path}", **kw)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def reap_running(self) -> int:
        """On startup, error-out any runs left 'running' by a previous (crashed) worker."""
        r = await self._req("POST", "/api/runs/reap-running")
        r.raise_for_status()
        return r.json().get("reaped", 0)

    async def claim_next(self) -> dict | None:
        """Claim the oldest pending run, or None if the queue is empty."""
        r = await self._req("POST", "/api/runs/next")
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    async def report(self, run_id: int, **fields):
        """Update a run: status, result, error, sandbox_path."""
        r = await self._req("POST", f"/api/runs/{run_id}", json=fields)
        r.raise_for_status()

    async def log(self, run_id: int, message: str):
        """Append a live progress message to the run."""
        r = await self._req("POST", f"/api/runs/{run_id}/log", json={"message": message})
        r.raise_for_status()

    async def resolve_workflow(self, name: str, run_id: int) -> dict | None:
        """Resolve a workflow by name for chaining, scoped to the run's user. None if
        not found or the name is ambiguous (the web side returns 404/409 for those)."""
        r = await self._req("GET", "/api/workflows/resolve",
                            params={"name": name, "run_id": run_id})
        if r.status_code in (404, 409):
            return None
        r.raise_for_status()
        return r.json()

    async def download_file(self, run_id: int, name: str, dest):
        r = await self._req("GET", f"/api/runs/{run_id}/files/{name}")
        r.raise_for_status()
        dest.write_bytes(r.content)

    async def aclose(self):
        await self.http.aclose()
