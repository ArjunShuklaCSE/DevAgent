import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from backend.event_bus import InMemoryEventBus
from backend.schemas import EventOut
from backend.services.event_store import EventPage, StoredEvent
from backend.sse import (
    InvalidLastEventIdError,
    format_event,
    parse_last_event_id,
    stream_run_events,
)
from core.run_status import RunStatus


class FakeReader:
    """Event log for one run, mutated by tests to simulate the worker."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        self.status = RunStatus.QUEUED
        self.events: list[StoredEvent] = []

    def append(self, event_type: str = "step_started", status: RunStatus | None = None) -> int:
        seq = len(self.events) + 1
        self.events.append(
            StoredEvent(
                seq=seq,
                run_id=self.run_id,
                step_id=None,
                event_type=event_type,
                payload={"n": seq},
                created_at=datetime.now(UTC),
            )
        )
        if status is not None:
            self.status = status
        return seq

    async def read_after(self, run_id: UUID, after_seq: int, limit: int) -> EventPage | None:
        if run_id != self.run_id:
            return None
        events = [e for e in self.events if e.seq > after_seq][:limit]
        return EventPage(events=events, run_status=self.status, last_seq=len(self.events))


async def _connected() -> bool:
    return False


def _ids(chunks: list[str]) -> list[int]:
    return [int(c.split("\n", 1)[0].removeprefix("id: ")) for c in chunks if c.startswith("id: ")]


async def _collect(reader: FakeReader, bus: InMemoryEventBus, after: int = 0) -> list[str]:
    stream = stream_run_events(
        reader.run_id,
        after,
        reader=reader,
        bus=bus,
        is_disconnected=_connected,
        keepalive_seconds=0.05,
    )
    return [chunk async for chunk in stream]


def test_parse_last_event_id() -> None:
    assert parse_last_event_id(None, None) == 0
    assert parse_last_event_id("12", None) == 12
    assert parse_last_event_id(" 7 ", 3) == 7  # header wins
    assert parse_last_event_id("", 3) == 3
    for bad in ["-1", "abc", "1.5"]:
        with pytest.raises(InvalidLastEventIdError):
            parse_last_event_id(bad, None)


def test_format_event_is_valid_sse() -> None:
    event = EventOut(
        seq=4,
        run_id=uuid4(),
        step_id=None,
        type="status_changed",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload={"to_status": "cloning"},
    )
    text = format_event(event)
    lines = text.split("\n")
    assert lines[0] == "id: 4"
    assert lines[1] == "event: status_changed"
    assert json.loads(lines[2].removeprefix("data: "))["payload"] == {"to_status": "cloning"}
    assert text.endswith("\n\n")


async def test_replays_everything_then_closes_when_terminal() -> None:
    reader = FakeReader(uuid4())
    for _ in range(4):
        reader.append()
    reader.append("status_changed", status=RunStatus.CANCELLED)

    chunks = await _collect(reader, InMemoryEventBus())

    assert chunks[0] == "retry: 3000\n\n"
    assert _ids(chunks) == [1, 2, 3, 4, 5]


async def test_resume_after_last_event_id_has_no_gaps_or_duplicates() -> None:
    reader = FakeReader(uuid4())
    for _ in range(9):
        reader.append()
    reader.append("status_changed", status=RunStatus.FAILED)
    bus = InMemoryEventBus()

    full = _ids(await _collect(reader, bus))
    first_part = full[:4]
    resumed = _ids(await _collect(reader, bus, after=first_part[-1]))

    assert first_part + resumed == full == list(range(1, 11))


async def test_live_events_are_delivered_on_wakeup() -> None:
    reader = FakeReader(uuid4())
    reader.append()
    bus = InMemoryEventBus()

    async def worker() -> None:
        await asyncio.sleep(0.01)
        await bus.publish(reader.run_id, reader.append())
        await asyncio.sleep(0.01)
        await bus.publish(reader.run_id, reader.append("status_changed", RunStatus.CANCELLED))

    chunks, _ = await asyncio.gather(_collect(reader, bus), worker())
    assert _ids(chunks) == [1, 2, 3]


async def test_sends_keepalive_comments_while_idle() -> None:
    reader = FakeReader(uuid4())
    reader.append()
    bus = InMemoryEventBus()

    async def finish_later() -> None:
        await asyncio.sleep(0.2)
        await bus.publish(reader.run_id, reader.append("status_changed", RunStatus.CANCELLED))

    chunks, _ = await asyncio.gather(_collect(reader, bus), finish_later())
    assert ": keepalive\n\n" in chunks
    assert _ids(chunks) == [1, 2]


async def test_stops_when_client_disconnects() -> None:
    reader = FakeReader(uuid4())
    reader.append()

    async def disconnected() -> bool:
        return True

    stream = stream_run_events(
        reader.run_id,
        0,
        reader=reader,
        bus=InMemoryEventBus(),
        is_disconnected=disconnected,
        keepalive_seconds=10,
    )
    chunks = [c async for c in stream]
    assert _ids(chunks) == [1]


async def test_unknown_run_ends_stream() -> None:
    reader = FakeReader(uuid4())
    stream = stream_run_events(
        uuid4(),
        0,
        reader=reader,
        bus=InMemoryEventBus(),
        is_disconnected=_connected,
        keepalive_seconds=10,
    )
    assert [c async for c in stream] == ["retry: 3000\n\n"]
