"""Repository and run use cases."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.errors import ConflictError, NotFoundError
from backend.event_bus import EventBus
from backend.queue import RunQueue
from backend.schemas import RepositoryCreate, RunCreate
from backend.services.event_store import append_event
from backend.services.sources import list_samples
from core.events import ErrorEvent, StatusChanged
from core.run_status import InvalidTransitionError, RunStatus, ensure_transition, is_terminal
from database.models import (
    AgentRun,
    AgentStep,
    Issue,
    IssueSource,
    Repository,
    RepositorySource,
    RunMode,
)

logger = structlog.get_logger(__name__)

SAMPLE_OWNER = "sample"


@dataclass(frozen=True)
class ListResult[T]:
    items: list[T]
    total: int


class RepositoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: RepositoryCreate, samples_root: Path) -> Repository:
        if data.sample is not None:
            if data.sample not in list_samples(samples_root):
                raise NotFoundError("Sample repository not found", {"sample": data.sample})
            source, owner, name = RepositorySource.LOCAL, SAMPLE_OWNER, data.sample
            clone_url = f"sample://{data.sample}"
        else:
            owner, name = data.owner_and_name()
            source, clone_url = RepositorySource.GITHUB, f"https://github.com/{owner}/{name}.git"
        existing = await self._session.scalar(
            select(Repository).where(
                Repository.source == source,
                func.lower(Repository.owner) == owner.lower(),
                func.lower(Repository.name) == name.lower(),
            )
        )
        if existing is not None:
            return existing
        repo = Repository(source=source, owner=owner, name=name, clone_url=clone_url)
        self._session.add(repo)
        await self._session.commit()
        return repo

    async def get(self, repository_id: UUID) -> Repository:
        repo = await self._session.get(Repository, repository_id)
        if repo is None:
            raise NotFoundError("Repository not found", {"repository_id": str(repository_id)})
        return repo

    async def list_page(self, limit: int, offset: int) -> ListResult[Repository]:
        total = await self._session.scalar(select(func.count()).select_from(Repository)) or 0
        rows = await self._session.scalars(
            select(Repository).order_by(Repository.created_at.desc()).limit(limit).offset(offset)
        )
        return ListResult(items=list(rows), total=total)


class RunService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        queue: RunQueue,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._queue = queue

    async def create(self, data: RunCreate) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            repo = await session.get(Repository, data.repository_id)
            if repo is None:
                raise NotFoundError(
                    "Repository not found", {"repository_id": str(data.repository_id)}
                )
            issue = Issue(
                repository_id=repo.id,
                source=IssueSource.PASTED,
                number=data.issue.number,
                title=data.issue.title,
                body=data.issue.body,
            )
            session.add(issue)
            await session.flush()
            run = AgentRun(
                repository_id=repo.id,
                issue_id=issue.id,
                mode=RunMode(data.mode),
                status=RunStatus.QUEUED,
                config={
                    "budget": data.budget.model_dump(mode="json"),
                    "model": data.model,
                    "dry_run_step_delay_ms": data.dry_run_step_delay_ms,
                },
            )
            session.add(run)
            await session.flush()
            seq = await append_event(
                session,
                run.id,
                StatusChanged(
                    from_status=None,
                    to_status=RunStatus.QUEUED,
                    reason="run created",
                    synthetic=run.mode is RunMode.DRY_RUN,
                ),
            )
            run_id = run.id
        await self._bus.publish(run_id, seq)

        try:
            await self._queue.enqueue_run(run_id)
        except (RedisError, OSError) as exc:
            logger.exception("run_enqueue_failed", run_id=str(run_id), error=str(exc))
            await mark_run_failed(
                self._session_factory,
                self._bus,
                run_id,
                "queue_unavailable",
                "Could not queue the run",
            )
        return await self.get(run_id)

    async def get(self, run_id: UUID) -> AgentRun:
        async with self._session_factory() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).options(selectinload(AgentRun.issue))
            )
            if run is None:
                raise NotFoundError("Run not found", {"run_id": str(run_id)})
            return run

    async def list_page(
        self,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        repository_id: UUID | None = None,
    ) -> ListResult[AgentRun]:
        conditions = []
        if status is not None:
            conditions.append(AgentRun.status == status)
        if repository_id is not None:
            conditions.append(AgentRun.repository_id == repository_id)
        async with self._session_factory() as session:
            total = (
                await session.scalar(select(func.count()).select_from(AgentRun).where(*conditions))
                or 0
            )
            rows = await session.scalars(
                select(AgentRun)
                .where(*conditions)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return ListResult(items=list(rows), total=total)

    async def steps(self, run_id: UUID) -> list[AgentStep]:
        async with self._session_factory() as session:
            if await session.get(AgentRun, run_id) is None:
                raise NotFoundError("Run not found", {"run_id": str(run_id)})
            rows = await session.scalars(
                select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)
            )
            return list(rows)

    async def cancel(self, run_id: UUID, reason: str | None) -> AgentRun:
        """Cancel a non-terminal run. The worker stops at its next step boundary."""
        async with self._session_factory() as session, session.begin():
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                raise NotFoundError("Run not found", {"run_id": str(run_id)})
            current = run.status
            try:
                ensure_transition(current, RunStatus.CANCELLED)
            except InvalidTransitionError as exc:
                raise ConflictError(
                    f"Run is already {current.value}", {"status": current.value}
                ) from exc
            now = datetime.now(UTC)
            run.status = RunStatus.CANCELLED
            run.status_reason = reason or "cancelled by user"
            run.finished_at = now
            run.started_at = run.started_at or now
            seq = await append_event(
                session,
                run_id,
                StatusChanged(
                    from_status=current,
                    to_status=RunStatus.CANCELLED,
                    reason=run.status_reason,
                    synthetic=run.mode is RunMode.DRY_RUN,
                ),
            )
        await self._bus.publish(run_id, seq)
        return await self.get(run_id)


async def mark_run_failed(
    session_factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    run_id: UUID,
    code: str,
    message: str,
) -> None:
    """Move a non-terminal run to ``failed`` with an ``error`` event. No-op if terminal."""
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or is_terminal(run.status):
            return
        current = run.status
        run.status = RunStatus.FAILED
        run.status_reason = message
        run.finished_at = datetime.now(UTC)
        await append_event(session, run_id, ErrorEvent(code=code, message=message))
        seq = await append_event(
            session,
            run_id,
            StatusChanged(from_status=current, to_status=RunStatus.FAILED, reason=message),
        )
    await bus.publish(run_id, seq)
