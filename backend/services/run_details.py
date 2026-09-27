"""Read models for the run detail and review pages: tool calls, LLM calls, tests, diff."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.errors import NotFoundError
from database.models import AgentRun, Approval, LlmCall, TestRun, ToolCallRecord


class RunDetails:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _require(self, session: AsyncSession, run_id: UUID) -> AgentRun:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise NotFoundError("Run not found", {"run_id": str(run_id)})
        return run

    async def tool_calls(self, run_id: UUID) -> Sequence[ToolCallRecord]:
        async with self._session_factory() as session:
            await self._require(session, run_id)
            rows = await session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run_id)
                .order_by(ToolCallRecord.created_at)
            )
            return rows.all()

    async def llm_calls(self, run_id: UUID) -> Sequence[LlmCall]:
        async with self._session_factory() as session:
            await self._require(session, run_id)
            rows = await session.scalars(
                select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.created_at)
            )
            return rows.all()

    async def test_runs(self, run_id: UUID) -> Sequence[TestRun]:
        async with self._session_factory() as session:
            await self._require(session, run_id)
            rows = await session.scalars(
                select(TestRun)
                .where(TestRun.run_id == run_id)
                .options(selectinload(TestRun.results))
                .order_by(TestRun.created_at)
            )
            return rows.all()

    async def approvals(self, run_id: UUID) -> Sequence[Approval]:
        async with self._session_factory() as session:
            await self._require(session, run_id)
            rows = await session.scalars(
                select(Approval).where(Approval.run_id == run_id).order_by(Approval.created_at)
            )
            return rows.all()

    async def diff(self, run_id: UUID) -> dict[str, Any]:
        async with self._session_factory() as session:
            run = await self._require(session, run_id)
            return {
                "run_id": run.id,
                "diff": run.final_diff,
                "sha256": run.final_diff_sha256,
                "review_flags": list(run.result.get("review_flags", [])),
                "validation": run.result.get("validation"),
            }
