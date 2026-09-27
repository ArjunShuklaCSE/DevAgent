"""The deployed agent path: API -> queue -> worker -> docker-proxy -> sandbox containers.

Uses the ``scripted`` model, which the Compose worker maps to the test cassette in
``tests/cassettes``, so no API key is needed. Also checks the failure path when a run
asks for a model the server cannot use.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

import docker
import httpx
import pytest

from tests.integration.sse_client import iter_sse

pytestmark = pytest.mark.integration

SAMPLES = Path(__file__).parents[2] / "sample_repos"
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

        # Everything the run detail and review pages read.
        diff = (await client.get(f"/api/v1/runs/{run_id}/diff")).json()
        assert diff["sha256"] == run["final_diff_sha256"]
        assert "slugger/slug.py" in diff["diff"]
        assert diff["validation"]["checks"][0]["name"] == "reproduction"
        tools = (await client.get(f"/api/v1/runs/{run_id}/tool-calls")).json()
        assert {t["tool_name"] for t in tools} >= {"search_text", "edit_file", "create_file"}
        llm = (await client.get(f"/api/v1/runs/{run_id}/llm-calls")).json()
        assert len(llm) == 14
        assert all(c["prompt_version"].startswith("v1:") for c in llm)
        tests = (await client.get(f"/api/v1/runs/{run_id}/test-runs")).json()
        assert tests[1]["kind"] == "reproduction"
        assert {r["outcome"] for r in tests[1]["results"]} == {"failed"}
        listing = (await client.get("/api/v1/runs", params={"limit": 5})).json()
        summary = next(r for r in listing["items"] if r["id"] == run_id)
        assert summary["repository"]["name"] == "slugger"
        assert summary["issue"]["number"] == 3

        # Approval is bound to the reviewed diff.
        stale = await client.post(f"/api/v1/runs/{run_id}/approve", json={"diff_sha256": "0" * 64})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "stale_diff"
        approved = await client.post(
            f"/api/v1/runs/{run_id}/approve",
            json={"diff_sha256": diff["sha256"], "comment": "looks right"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        again = await client.post(f"/api/v1/runs/{run_id}/reject", json={})
        assert again.status_code == 409
        decisions = (await client.get(f"/api/v1/runs/{run_id}/approvals")).json()
        assert [(d["decision"], d["diff_sha256"]) for d in decisions] == [
            ("approved", diff["sha256"])
        ]

        await _check_patch_delivery(client, run_id)

        await _check_trace(client, run_id, llm_calls=len(llm), tool_calls=len(tools))


async def _check_trace(
    client: httpx.AsyncClient, run_id: str, *, llm_calls: int, tool_calls: int
) -> None:
    """The exported trace carries the whole run in one document."""
    trace = await client.get(f"/api/v1/runs/{run_id}/trace")
    assert trace.status_code == 200
    assert run_id in trace.headers["content-disposition"]
    body = trace.json()
    assert body["format"] == "devagent-trace/v1"
    assert body["run"]["id"] == run_id
    assert len(body["llm_calls"]) == llm_calls
    assert len(body["tool_calls"]) == tool_calls
    assert [e["seq"] for e in body["events"]] == list(range(1, len(body["events"]) + 1))
    assert body["diff"]["sha256"] == body["run"]["final_diff_sha256"]
    assert [a["decision"] for a in body["approvals"]] == ["approved"]
    assert not body["events_truncated"]


async def _check_patch_delivery(client: httpx.AsyncClient, run_id: str) -> None:
    # The worker's publish job: a sample repository has no GitHub remote, so the
    # approved fix is delivered as a patch that applies to the sample.
    delivery = None
    for _ in range(40):
        delivery = (await client.get(f"/api/v1/runs/{run_id}")).json()["result"].get("delivery")
        if delivery:
            break
        await asyncio.sleep(0.5)
    assert delivery is not None
    assert (delivery["kind"], delivery["code"]) == ("patch", "not_a_github_repository")
    patch = await client.get(f"/api/v1/runs/{run_id}/patch")
    assert patch.status_code == 200
    assert "attachment" in patch.headers["content-disposition"]
    assert await _patch_applies(patch.content)


async def _patch_applies(patch: bytes) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "slugger"
        shutil.copytree(SAMPLES / "slugger", repo, ignore=shutil.ignore_patterns("__pycache__"))
        (repo / "fix.patch").write_bytes(patch)
        process = await asyncio.create_subprocess_exec(
            "git", "apply", "--check", "fix.patch", cwd=repo, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return True


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
        # The worker closes the open step after it has killed the containers.
        open_steps: list[str] = []
        for _ in range(20):
            steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
            open_steps = [s["state"] for s in steps if s["status"] == "running"]
            if not open_steps:
                break
            await asyncio.sleep(0.5)
        assert open_steps == []
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        assert run["status"] == "cancelled"
