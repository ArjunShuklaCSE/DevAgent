"""Deliver an approved run: a draft pull request when possible, otherwise a patch file.

Called by the worker's ``publish_run`` job after a human approved the exact diff. The
outcome is recorded in ``result.delivery``; the patch is always downloadable from
``GET /runs/{id}/patch``.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunUpdate
from backend.config import Settings
from backend.event_bus import EventBus
from backend.github.client import GitHubError, GitHubRateLimitError
from backend.github.publisher import PullRequestPublisher, PullRequestSpec
from backend.services.auth import AuthService
from backend.services.recorder import DbRunRecorder
from core.run_status import RunStatus
from database.models import (
    AgentRun,
    Approval,
    ApprovalDecision,
    Issue,
    PullRequest,
    PullRequestState,
    Repository,
    RepositorySource,
)

logger = structlog.get_logger(__name__)


def _patch_delivery(reason: str, code: str) -> dict[str, Any]:
    return {
        "delivery": {
            "kind": "patch",
            "code": code,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        }
    }


def _blocker(
    run: AgentRun,
    repo: Repository | None,
    issue: Issue | None,
    approval: Approval | None,
    *,
    has_token: bool,
) -> tuple[str, str] | None:
    """Why this run gets a patch instead of a PR, as (reason, code); None if a PR can be tried."""
    if repo is None or issue is None or approval is None or not run.final_diff:
        return "the run has no approved diff", "no_diff"
    if repo.source is not RepositorySource.GITHUB:
        return (
            "local sample repository: download the patch and apply it with `git am`",
            "not_a_github_repository",
        )
    if not has_token:
        return (
            "no GitHub credentials: sign in with GitHub or set DEVAGENT_GITHUB_TOKEN, "
            "then retry, or download the patch",
            "github_not_configured",
        )
    if run.base_commit_sha is None:
        return "the base commit was not recorded", "no_base_commit"
    return None


async def publish_run(
    run_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    auth: AuthService,
    settings: Settings,
) -> str:
    """Open a draft PR for an approved run. Returns the delivery kind or a skip reason."""
    log = logger.bind(run_id=str(run_id))
    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            return "missing"
        if run.status is not RunStatus.APPROVED:
            log.info("publish_skipped", status=run.status.value)
            return f"skipped:{run.status.value}"
        repo = await session.get(Repository, run.repository_id)
        issue = await session.get(Issue, run.issue_id)
        approval = await session.scalar(
            select(Approval)
            .where(Approval.run_id == run_id, Approval.decision == ApprovalDecision.APPROVED)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
    recorder = DbRunRecorder(session_factory, bus, run_id)
    token = await auth.token_for(approval.user_id) if approval is not None else None
    blocker = _blocker(run, repo, issue, approval, has_token=token is not None)
    if blocker is not None:
        await recorder.update_run(RunUpdate(result=_patch_delivery(*blocker)))
        return "patch"
    if (  # already covered by _blocker; narrows the types
        repo is None
        or issue is None
        or approval is None
        or run.final_diff is None
        or run.base_commit_sha is None
    ):
        return "patch"

    pr_text: dict[str, Any] = run.result.get("pull_request") or {}
    spec = PullRequestSpec(
        owner=repo.owner,
        repo=repo.name,
        base_commit=run.base_commit_sha,
        diff=run.final_diff,
        approved_sha256=approval.diff_sha256,
        title=str(pr_text.get("title") or issue.title)[:250],
        body=str(pr_text.get("body") or ""),
        commit_message=str(pr_text.get("commit_message") or issue.title),
        issue_number=issue.number,
        issue_title=issue.title,
        run_id=str(run_id),
        author_name=settings.github_commit_name,
        author_email=settings.github_commit_email,
    )

    await recorder.transition(
        RunStatus.CREATING_PR, f"opening a draft PR on {repo.owner}/{repo.name}"
    )
    step = await recorder.start_step(RunStatus.CREATING_PR)
    try:
        async with auth.client(token) as client:
            published = await PullRequestPublisher(client).publish(spec)
    except GitHubError as exc:
        message = exc.message
        if isinstance(exc, GitHubRateLimitError) and exc.reset_at is not None:
            message += f" (resets at {exc.reset_at.isoformat()})"
        log.warning("publish_failed", code=exc.code, error=message)
        await recorder.finish_step(
            step, "failed", message, error={"code": exc.code, "message": message}
        )
        await recorder.update_run(RunUpdate(result=_patch_delivery(message, exc.code)))
        await recorder.transition(RunStatus.APPROVED, f"PR not opened: {message}"[:2000])
        return "patch"

    async with session_factory() as session, session.begin():
        session.add(
            PullRequest(
                run_id=run_id,
                repository_id=repo.id,
                number=published.number,
                url=published.url,
                branch=published.branch,
                head_sha=published.head_sha,
                state=PullRequestState.OPEN,
                is_draft=True,
            )
        )
    await recorder.finish_step(
        step,
        "completed",
        f"Opened draft PR #{published.number} from {published.branch}",
        {"number": published.number, "url": published.url, "branch": published.branch},
    )
    await recorder.update_run(
        RunUpdate(
            result={
                "delivery": {
                    "kind": "pull_request",
                    "number": published.number,
                    "url": published.url,
                    "branch": published.branch,
                    "base_branch": published.base_branch,
                    "base_moved": published.base_moved,
                    "at": datetime.now(UTC).isoformat(),
                }
            }
        )
    )
    await recorder.transition(RunStatus.PR_CREATED, f"draft PR #{published.number} opened")
    return "pull_request"
