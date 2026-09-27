"""End-to-end against the running stack: API → queue → worker → Postgres → SSE.

Proves the Phase 1 acceptance criteria: creating a dry run streams events over SSE, and
reconnecting with ``Last-Event-ID`` resumes exactly where the client left off.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from agent.dry_run import DRY_RUN_PATH
from tests.integration.sse_client import SseMessage, iter_sse

pytestmark = pytest.mark.integration

API_URL = os.environ.get("DEVAGENT_TEST_API_URL", "http://localhost:8000")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=API_URL, timeout=30) as c:
        yield c


async def _create_dry_run(client: httpx.AsyncClient, delay_ms: int) -> str:
    repo = await client.post("/api/v1/repositories", json={"url": "https://github.com/o/sample"})
    assert repo.status_code == 201, repo.text
    run = await client.post(
        "/api/v1/runs",
        json={
            "repository_id": repo.json()["id"],
            "issue": {"title": "Dry run", "body": "synthetic"},
            "mode": "dry_run",
            "dry_run_step_delay_ms": delay_ms,
        },
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "queued"
    return str(run.json()["id"])


async def _read(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    last_event_id: int | None = None,
    stop_after: int | None = None,
    stop_at_status: str | None = None,
) -> list[SseMessage]:
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    messages: list[SseMessage] = []
    async with client.stream("GET", f"/api/v1/runs/{run_id}/events", headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for message in iter_sse(resp):
            messages.append(message)
            if stop_after is not None and len(messages) >= stop_after:
                break
            if (
                stop_at_status is not None
                and message.event == "status_changed"
                and message.data["payload"]["to_status"] == stop_at_status
            ):
                break
    return messages


async def test_dry_run_streams_and_resumes_with_last_event_id(client: httpx.AsyncClient) -> None:
    run_id = await _create_dry_run(client, delay_ms=150)

    # Connect live, read a few events, then drop the connection mid-run.
    first = await _read(client, run_id, stop_after=6)
    assert [m.id for m in first] == list(range(1, 7))

    # Reconnect from the last id we saw; the run keeps going meanwhile.
    rest = await _read(
        client, run_id, last_event_id=first[-1].id, stop_at_status="awaiting_approval"
    )
    assert rest[0].id == first[-1].id + 1

    combined = [m.id for m in first + rest]
    assert combined == list(range(1, len(combined) + 1)), "gap or duplicate after resume"

    # Every event is labeled as synthetic dry-run output.
    assert all(m.data["payload"]["synthetic"] for m in first + rest)

    # Status events follow the state machine path exactly.
    statuses = [m.data["payload"]["to_status"] for m in first + rest if m.event == "status_changed"]
    assert statuses == ["queued", *[s.value for s in DRY_RUN_PATH], "awaiting_approval"]

    # A full replay from scratch returns the identical sequence.
    replay = await _read(client, run_id, stop_at_status="awaiting_approval")
    assert [(m.id, m.event) for m in replay] == [(m.id, m.event) for m in first + rest]

    # Cancel ends the run; the stream then closes on its own after the final event.
    cancel = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "test done"})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    tail = await _read(client, run_id, last_event_id=combined[-1])
    assert [m.data["payload"]["to_status"] for m in tail] == ["cancelled"]


async def test_run_detail_steps_and_listing(client: httpx.AsyncClient) -> None:
    run_id = await _create_dry_run(client, delay_ms=0)
    await _read(client, run_id, stop_at_status="awaiting_approval")

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "awaiting_approval"
    assert run["step_count"] == len(DRY_RUN_PATH)
    assert run["started_at"] is not None
    assert run["config"]["budget"]["max_steps"] == 40

    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
    assert [s["state"] for s in steps] == [s.value for s in DRY_RUN_PATH]
    assert all(s["synthetic"] and s["status"] == "completed" for s in steps)
    assert all(s["summary"].startswith("[dry run]") for s in steps)

    listing = (await client.get("/api/v1/runs", params={"status": "awaiting_approval"})).json()
    assert run_id in {r["id"] for r in listing["items"]}

    assert (await client.post(f"/api/v1/runs/{run_id}/cancel")).status_code == 200
    again = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"


async def test_cancel_mid_run_stops_the_worker(client: httpx.AsyncClient) -> None:
    run_id = await _create_dry_run(client, delay_ms=300)
    await _read(client, run_id, stop_at_status="localizing")

    assert (await client.post(f"/api/v1/runs/{run_id}/cancel")).status_code == 200
    events = await _read(client, run_id)  # closes because the run is terminal

    statuses = [m.data["payload"]["to_status"] for m in events if m.event == "status_changed"]
    assert statuses[-1] == "cancelled"
    assert "awaiting_approval" not in statuses
    # Nothing is appended after the terminal event, even though a step was in flight.
    assert events[-1].event == "status_changed"
    assert events[-1].data["payload"]["to_status"] == "cancelled"
    # The in-flight step is closed as "skipped" when the worker wakes from it.
    for _ in range(50):
        steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
        if steps[-1]["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert steps[-1]["status"] in {"skipped", "completed"}
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "cancelled"
    assert run["step_count"] < len(DRY_RUN_PATH)


async def test_not_found_errors_use_envelope(client: httpx.AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    for path in (f"/api/v1/runs/{missing}", f"/api/v1/runs/{missing}/events"):
        response = await client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
