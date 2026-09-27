"""Read models for the run detail and review pages: tool calls, LLM calls, tests, diff."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.errors import NotFoundError
from backend.github.patch import render_patch
from database.models import (
    AgentRun,
    Approval,
    Issue,
    LlmCall,
    PullRequest,
    TestRun,
    ToolCallRecord,
)


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

    async def pull_request(self, run_id: UUID) -> PullRequest:
        async with self._session_factory() as session:
            await self._require(session, run_id)
            pull = await session.scalar(select(PullRequest).where(PullRequest.run_id == run_id))
            if pull is None:
                raise NotFoundError(
                    "No pull request was opened for this run", {"run_id": str(run_id)}
                )
            return pull

    async def patch(self, run_id: UUID, author_name: str, author_email: str) -> tuple[str, str]:
        """``(filename, git am patch)`` for the run's final diff."""
        async with self._session_factory() as session:
            run = await self._require(session, run_id)
            issue = await session.get(Issue, run.issue_id)
            if not run.final_diff:
                raise NotFoundError("This run has no diff", {"run_id": str(run_id)})
            pr_text: dict[str, Any] = run.result.get("pull_request") or {}
            title = str(pr_text.get("title") or (issue.title if issue else "DevAgent fix"))
            message = str(pr_text.get("commit_message") or title)
            body = message.split("\n", 1)[1].strip() if "\n" in message else ""
            if issue is not None and issue.number is not None:
                body = f"{body}\n\nFixes #{issue.number}.".strip()
            patch = render_patch(
                run.final_diff,
                subject=message.split("\n", 1)[0] or title,
                body=body,
                author_name=author_name,
                author_email=author_email,
                date=run.finished_at or run.created_at,
            )
        stem = (
            f"issue-{issue.number}"
            if issue is not None and issue.number
            else f"run-{str(run_id)[:8]}"
        )
        return f"devagent-{stem}.patch", patch
