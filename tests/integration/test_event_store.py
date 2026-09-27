"""Event sequence numbers are gap-free and unique under concurrent writers."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunCancelledError
from backend.event_bus import InMemoryEventBus
from backend.services.event_store import SqlEventReader, append_event
from backend.services.recorder import DbRunRecorder
from core.events import CommandOutput
from core.run_status import RunStatus
from database.engine import create_engine, create_session_factory
from database.migrate import build_config
from database.models import (
    AgentRun,
    AgentStep,
    Issue,
    IssueSource,
    Repository,
    RepositorySource,
    RunMode,
    StepStatus,
)
from tests.integration.db import temporary_database

pytestmark = pytest.mark.integration


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with temporary_database() as url:
        await asyncio.to_thread(command.upgrade, build_config(url), "head")
        engine = create_engine(url)
        try:
            yield create_session_factory(engine)
        finally:
            await engine.dispose()


async def _new_run(factory: async_sessionmaker[AsyncSession]) -> AgentRun:
    async with factory() as session, session.begin():
        repo = Repository(source=RepositorySource.GITHUB, owner="o", name="r", clone_url="u")
        session.add(repo)
        await session.flush()
        issue = Issue(repository_id=repo.id, source=IssueSource.PASTED, title="t")
        session.add(issue)
        await session.flush()
        run = AgentRun(
            repository_id=repo.id, issue_id=issue.id, mode=RunMode.DRY_RUN, status=RunStatus.QUEUED
        )
        session.add(run)
    return run


async def test_concurrent_appends_get_contiguous_sequence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _new_run(session_factory)

    async def append(i: int) -> int:
        async with session_factory() as session, session.begin():
            return await append_event(
                session, run.id, CommandOutput(stream="stdout", text=f"line {i}")
            )

    seqs = await asyncio.gather(*(append(i) for i in range(40)))

    assert sorted(seqs) == list(range(1, 41))
    page = await SqlEventReader(session_factory).read_after(run.id, 0, 100)
    assert page is not None
    assert [e.seq for e in page.events] == list(range(1, 41))
    assert page.last_seq == 40


async def test_recorder_refuses_to_continue_a_cancelled_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _new_run(session_factory)
    bus = InMemoryEventBus()
    recorder = DbRunRecorder(session_factory, bus, run.id)
    await recorder.transition(RunStatus.CLONING)

    async with session_factory() as session, session.begin():
        stored = await session.scalar(select(AgentRun).where(AgentRun.id == run.id))
        assert stored is not None
        stored.status = RunStatus.CANCELLED

    with pytest.raises(RunCancelledError):
        await recorder.transition(RunStatus.ANALYZING_REPO)
    with pytest.raises(RunCancelledError):
        await recorder.start_step(RunStatus.ANALYZING_REPO)
    with pytest.raises(RunCancelledError):
        await recorder.emit(CommandOutput(stream="stdout", text="late"))
    assert bus.published == [(run.id, 1)]


async def test_step_finishing_after_cancel_emits_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _new_run(session_factory)
    bus = InMemoryEventBus()
    recorder = DbRunRecorder(session_factory, bus, run.id)
    await recorder.transition(RunStatus.CLONING)
    step = await recorder.start_step(RunStatus.CLONING)
    await recorder.transition(RunStatus.CANCELLED, reason="user")

    with pytest.raises(RunCancelledError):
        await recorder.finish_step(step, "completed", "done")

    page = await SqlEventReader(session_factory).read_after(run.id, 0, 100)
    assert page is not None
    assert [e.event_type for e in page.events] == [
        "status_changed",
        "step_started",
        "status_changed",
    ]
    async with session_factory() as session:
        stored = await session.get(AgentStep, step.id)
        assert stored is not None
        assert stored.status is StepStatus.SKIPPED


async def test_close_open_steps_after_interruption(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _new_run(session_factory)
    recorder = DbRunRecorder(session_factory, InMemoryEventBus(), run.id)
    await recorder.transition(RunStatus.CLONING)
    step = await recorder.start_step(RunStatus.CLONING)
    await recorder.transition(RunStatus.CANCELLED)

    assert await recorder.close_open_steps("run is cancelled") == 1

    async with session_factory() as session:
        stored = await session.get(AgentStep, step.id)
        assert stored is not None
        assert stored.status is StepStatus.SKIPPED
        assert stored.finished_at is not None
