"""Append-only run event log with per-run sequence numbers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.events import EventPayload
from core.run_status import RunStatus
from database.models import AgentRun, RunEvent

_PAYLOAD_ADAPTER: TypeAdapter[EventPayload] = TypeAdapter(EventPayload)


@dataclass(frozen=True)
class StoredEvent:
    seq: int
    run_id: UUID
    step_id: UUID | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class EventPage:
    events: list[StoredEvent]
    run_status: RunStatus
    last_seq: int


async def append_event(
    session: AsyncSession, run_id: UUID, payload: EventPayload, step_id: UUID | None = None
) -> int:
    """Insert an event in the caller's transaction and return its sequence number.

    The counter row update takes a row lock on the run, so concurrent writers for the
    same run get strictly increasing, gap-free sequence numbers. Callers publish the
    returned seq on the event bus only after their transaction commits.
    """
    seq = await session.scalar(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(last_event_seq=AgentRun.last_event_seq + 1)
        .returning(AgentRun.last_event_seq)
    )
    if seq is None:
        raise LookupError(f"run {run_id} does not exist")
    session.add(
        RunEvent(
            run_id=run_id,
            seq=seq,
            step_id=step_id,
            event_type=payload.type,
            payload=payload.model_dump(mode="json"),
        )
    )
    await session.flush()
    return int(seq)


def parse_payload(data: dict[str, Any]) -> EventPayload:
    return _PAYLOAD_ADAPTER.validate_python(data)


class SqlEventReader:
    """Reads events after a sequence number together with the run's current status."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def read_after(self, run_id: UUID, after_seq: int, limit: int) -> EventPage | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentRun.status, AgentRun.last_event_seq).where(AgentRun.id == run_id)
                )
            ).one_or_none()
            if row is None:
                return None
            records = await session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                .order_by(RunEvent.seq)
                .limit(limit)
            )
            events = [
                StoredEvent(
                    seq=r.seq,
                    run_id=r.run_id,
                    step_id=r.step_id,
                    event_type=r.event_type.value,
                    payload=r.payload,
                    created_at=r.created_at,
                )
                for r in records
            ]
        return EventPage(events=events, run_status=row.status, last_seq=row.last_event_seq)
