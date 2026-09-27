"""The deployed agent path: API -> queue -> worker -> docker-proxy -> sandbox containers.

Uses the ``scripted`` model, which the Compose worker maps to the test cassette in
``tests/cassettes``, so no API key is needed. Also checks the failure path when a run
asks for a model the server cannot use.
"""

import asyncio
import os
from collections.abc import Sequence

import docker
import httpx
import pytest

from tests.integration.sse_client import iter_sse

pytestmark = pytest.mark.integration

API_URL = os.environ.get("DEVAGENT_TEST_API_URL", "http://localhost:8000")
ISSUE = {
    "title": "slugify crashes on titles without letters",
    "body": 'slugify("") and slugify("!!!") raise IndexError: list index out of range.',
    "number": 3,
}
FINAL = {"awaiting_approval", "failed", "budget_exceeded", "timed_out", "cancelled"}


async def _sample(client: httpx.AsyncClient) -> str:
    samples = (await client.get("/api/v1/repositories/samples")).json()
    assert "slugger" in samples
    repo = await client.post("/api/v1/repositories", json={"sample": "slugger"})
    assert repo.status_code == 201, repo.text
    assert repo.json()["source"] == "local"
    return str(repo.json()["id"])


async def _wait_final(client: httpx.AsyncClient, run_id: str, limit_s: float = 240) -> list[str]:
    """Follow the SSE stream until the run reaches a final or review state."""
    statuses: list[str] = []
    async with (
        asyncio.timeout(limit_s),
        client.stream("GET", f"/api/v1/runs/{run_id}/events") as response,
    ):
        async for message in iter_sse(response):
            if message.event == "status_changed":
                statuses.append(message.data["payload"]["to_status"])
                if statuses[-1] in FINAL:
                    break
    return statuses


async def test_scripted_agent_run_through_the_deployed_stack() -> None:
    async with httpx.AsyncClient(base_url=API_URL, timeout=httpx.Timeout(300)) as client:
        created = await client.post(
            "/api/v1/runs",
            json={"repository_id": await _sample(client), "issue": ISSUE, "model": "scripted"},
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]

        statuses = await _wait_final(client, run_id)

        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        assert statuses[-1] == "awaiting_approval", run["status_reason"]
        assert "debugging" in statuses
        assert run["fix_attempts"] == 1
        assert run["config"]["provider"] == "scripted"
        assert run["sandbox_image_digest"].startswith("sha256:")
        assert run["result"]["pull_request"]["title"]
        assert run["final_diff_sha256"]
        steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
        assert not any(s["synthetic"] for s in steps)
        assert steps[-1]["state"] == "validating"


async def test_run_with_an_unusable_model_fails_clearly() -> None:
    async with httpx.AsyncClient(base_url=API_URL, timeout=httpx.Timeout(60)) as client:
        created = await client.post(
            "/api/v1/runs",
            json={"repository_id": await _sample(client), "issue": ISSUE, "model": "no-such-model"},
        )
        run_id = created.json()["id"]
        statuses = await _wait_final(client, run_id, limit_s=30)
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert statuses[-1] == "failed"
    assert "no-such-model" in run["status_reason"]


async def test_cancel_stops_the_worker_and_its_sandbox_containers() -> None:
    async with httpx.AsyncClient(base_url=API_URL, timeout=httpx.Timeout(120)) as client:
        created = await client.post(
            "/api/v1/runs",
            json={"repository_id": await _sample(client), "issue": ISSUE, "model": "scripted"},
        )
        run_id = created.json()["id"]
        # Cancel while the baseline install/tests run in the sandbox.
        async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as response:
            async for message in iter_sse(response):
                if message.event == "command_output":
                    break
        cancel = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "test"})
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        docker_client = docker.from_env()
        try:
            remaining: Sequence[object] = []
            for _ in range(20):
                remaining = docker_client.containers.list(
                    all=True, filters={"label": f"devagent.run_id={run_id}"}
                )
                if not remaining:
                    break
                await asyncio.sleep(0.5)
        finally:
            docker_client.close()
        assert remaining == []
        steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
        assert all(s["status"] != "running" for s in steps)
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        assert run["status"] == "cancelled"
