"""Server-Sent Events stream for a run, with ``Last-Event-ID`` resume (ADR 0005).

Algorithm: subscribe to wake-ups *before* the first read (so nothing committed after
the read is missed), replay every event with ``seq > last_event_id`` from Postgres, then
alternate between waiting for a wake-up (or keepalive timeout) and reading newer rows.
The stream ends after the run reaches a terminal status and its last event was sent.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol
from uuid import UUID

from backend.event_bus import EventBus
from backend.schemas import EventOut
from backend.services.event_store import EventPage
from core.run_status import is_terminal

RETRY_MS = 3000
PAGE_SIZE = 500


class EventReader(Protocol):
    async def read_after(self, run_id: UUID, after_seq: int, limit: int) -> EventPage | None: ...


class InvalidLastEventIdError(ValueError):
    pass


def parse_last_event_id(header: str | None, query: int | None) -> int:
    """The ``Last-Event-ID`` header wins over the ``after`` query parameter."""
    if header is not None and header.strip() != "":
        value = header.strip()
        if not value.isdigit():
            raise InvalidLastEventIdError(
                f"Last-Event-ID must be a non-negative integer: {value!r}"
            )
        return int(value)
    return query if query is not None else 0


def format_event(event: EventOut) -> str:
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


async def stream_run_events(
    run_id: UUID,
    after_seq: int,
    *,
    reader: EventReader,
    bus: EventBus,
    is_disconnected: Callable[[], Awaitable[bool]],
    keepalive_seconds: float = 15.0,
) -> AsyncIterator[str]:
    yield f"retry: {RETRY_MS}\n\n"
    last_sent = after_seq
    async with bus.subscribe(run_id) as wakeups:
        while True:
            page = await reader.read_after(run_id, last_sent, PAGE_SIZE)
            if page is None:  # run deleted mid-stream
                return
            for stored in page.events:
                yield format_event(
                    EventOut(
                        seq=stored.seq,
                        run_id=stored.run_id,
                        step_id=stored.step_id,
                        type=stored.event_type,
                        created_at=stored.created_at,
                        payload=stored.payload,
                    )
                )
                last_sent = stored.seq
            if len(page.events) == PAGE_SIZE:
                continue  # more backlog to replay
            if is_terminal(page.run_status) and last_sent >= page.last_seq:
                return
            if await is_disconnected():
                return
            if not await wakeups.wait(keepalive_seconds):
                yield ": keepalive\n\n"
