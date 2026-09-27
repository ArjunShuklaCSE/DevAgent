"""Database-backed ``RunRecorder``: persists transitions, steps and events atomically."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunCancelledError, StepHandle, StepOutcome
from backend.event_bus import EventBus
from backend.services.event_store import append_event
from core.events import EventPayload, StatusChanged, StepCompleted, StepStarted
from core.run_status import RunStatus, ensure_transition, is_terminal
from database.models import AgentRun, AgentStep, RunMode, StepStatus

_OUTCOME_TO_STATUS: dict[StepOutcome, StepStatus] = {
    "completed": StepStatus.COMPLETED,
    "failed": StepStatus.FAILED,
    "skipped": StepStatus.SKIPPED,
}


class DbRunRecorder:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], bus: EventBus, run_id: UUID
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self.run_id = run_id
        self._step_started: dict[UUID, float] = {}

    async def _locked_run(self, session: AsyncSession) -> AgentRun:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == self.run_id).with_for_update()
        )
        if run is None:
            raise RunCancelledError(f"run {self.run_id} no longer exists")
        return run

    async def transition(self, target: RunStatus, reason: str | None = None) -> None:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session)
            current = run.status
            if is_terminal(current):
                raise RunCancelledError(f"run is {current.value}")
            ensure_transition(current, target)
            now = datetime.now(UTC)
            run.status = target
            run.status_reason = reason
            if run.started_at is None:
                run.started_at = now
            if is_terminal(target):
                run.finished_at = now
            seq = await append_event(
                session,
                self.run_id,
                StatusChanged(
                    from_status=current, to_status=target, reason=reason, synthetic=_synthetic(run)
                ),
            )
        await self._bus.publish(self.run_id, seq)

    async def start_step(self, state: RunStatus, *, synthetic: bool = False) -> StepHandle:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session)
            if is_terminal(run.status):
                raise RunCancelledError(f"run is {run.status.value}")
            sequence = await session.scalar(
                update(AgentRun)
                .where(AgentRun.id == self.run_id)
                .values(step_count=AgentRun.step_count + 1)
                .returning(AgentRun.step_count)
            )
            assert sequence is not None  # noqa: S101 - row is locked above
            step = AgentStep(
                run_id=self.run_id,
                sequence=sequence,
                state=state,
                status=StepStatus.RUNNING,
                synthetic=synthetic,
                started_at=datetime.now(UTC),
            )
            session.add(step)
            await session.flush()
            handle = StepHandle(id=step.id, sequence=sequence, state=state)
            seq = await append_event(
                session,
                self.run_id,
                StepStarted(
                    step_id=step.id, step_sequence=sequence, state=state, synthetic=synthetic
                ),
                step_id=step.id,
            )
        self._step_started[handle.id] = time.perf_counter()
        await self._bus.publish(self.run_id, seq)
        return handle

    async def finish_step(
        self,
        step: StepHandle,
        outcome: StepOutcome,
        summary: str,
        output: dict[str, Any] | None = None,
    ) -> None:
        started = self._step_started.pop(step.id, time.perf_counter())
        duration_ms = int((time.perf_counter() - started) * 1000)
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session)
            record = await session.get(AgentStep, step.id)
            if record is None:
                raise RunCancelledError(f"step {step.id} no longer exists")
            record.finished_at = datetime.now(UTC)
            record.duration_ms = duration_ms
            if is_terminal(run.status):
                # Close the step for the record, but emit nothing after the final event.
                record.status = StepStatus.SKIPPED
                record.summary = f"Interrupted: run is {run.status.value}"
                cancelled = True
            else:
                record.status = _OUTCOME_TO_STATUS[outcome]
                record.summary = summary
                record.output = output or {}
                cancelled = False
                seq = await append_event(
                    session,
                    self.run_id,
                    StepCompleted(
                        step_id=step.id,
                        step_sequence=step.sequence,
                        state=step.state,
                        outcome=outcome,
                        duration_ms=duration_ms,
                        summary=summary,
                        synthetic=record.synthetic,
                    ),
                    step_id=step.id,
                )
        if cancelled:
            raise RunCancelledError(f"run is {run.status.value}")
        await self._bus.publish(self.run_id, seq)

    async def emit(self, payload: EventPayload, step: StepHandle | None = None) -> None:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session)
            if is_terminal(run.status):
                raise RunCancelledError(f"run is {run.status.value}")
            seq = await append_event(session, self.run_id, payload, step.id if step else None)
        await self._bus.publish(self.run_id, seq)

    async def close_open_steps(self, reason: str) -> int:
        """Mark steps left ``running`` by an interrupted executor as ``skipped``.

        Emits no events: this only runs after the run is already terminal.
        """
        async with self._session_factory() as session, session.begin():
            closed = await session.scalars(
                update(AgentStep)
                .where(AgentStep.run_id == self.run_id, AgentStep.status == StepStatus.RUNNING)
                .values(
                    status=StepStatus.SKIPPED,
                    summary=f"Interrupted: {reason}",
                    finished_at=datetime.now(UTC),
                )
                .returning(AgentStep.id)
            )
            return len(closed.all())


def _synthetic(run: AgentRun) -> bool:
    return run.mode is RunMode.DRY_RUN
