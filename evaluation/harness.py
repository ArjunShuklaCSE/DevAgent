"""Run the agent over a benchmark dataset and record scored results (spec 13).

Each case runs as a normal agent run (same orchestrator, sandbox, database records and
trace pages as a run started from the dashboard), executed in-process instead of
through the queue. When a run reaches ``awaiting_approval`` its diff is scored against
the hidden tests in a fresh sandbox. Evaluation runs are never approved or published:
they stay in ``awaiting_approval`` like any other run.
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunCancelledError
from backend.config import Settings
from backend.event_bus import EventBus
from backend.llm_factory import load_pricing
from backend.schemas import RepositoryCreate, RunBudget
from backend.services.event_store import append_event
from backend.services.recorder import DbRunRecorder
from backend.services.runs import RepositoryService, mark_run_failed
from backend.services.sources import source_for
from backend.worker.agent_job import run_agent
from core.events import StatusChanged
from core.run_status import RunStatus
from database.models import (
    AgentRun,
    EvalCase,
    EvalDataset,
    EvalResult,
    EvalRun,
    EvalRunStatus,
    FailureCategory,
    Issue,
    IssueSource,
    Repository,
    RunMode,
)
from evaluation.dataset import Case, Dataset, DatasetError
from evaluation.scoring import Score, Scorer
from llm.types import LLMError
from sandbox.docker_sandbox import DockerSandbox, SandboxError
from workspace.clone import CloneConfig
from workspace.limits import RepoLimits

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HarnessOptions:
    model: str
    label: str | None = None
    repeats: int = 1
    concurrency: int = 1
    case_ids: Sequence[str] = ()
    budget: RunBudget = field(default_factory=RunBudget)


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    repeat: int
    agent_run_id: UUID
    status: RunStatus
    resolved: bool
    failure_category: FailureCategory | None


def categorize(
    status: RunStatus, failure: dict[str, Any] | None, score: Score | None
) -> FailureCategory | None:
    """Map a finished run and its score to the failure taxonomy (None when resolved)."""
    if score is not None and score.resolved:
        return None
    if status is RunStatus.AWAITING_APPROVAL and score is not None:
        return _score_category(score)
    return _run_category(status, failure)


def _run_category(status: RunStatus, failure: dict[str, Any] | None) -> FailureCategory:
    if status is RunStatus.BUDGET_EXCEEDED:
        return FailureCategory.BUDGET_EXCEEDED
    if status is RunStatus.TIMED_OUT:
        return FailureCategory.TIMEOUT
    raw = (failure or {}).get("category") if status is RunStatus.FAILED else None
    try:
        return FailureCategory(str(raw))
    except ValueError:
        return FailureCategory.INFRA


def _score_category(score: Score) -> FailureCategory:
    if score.error_kind is not None:
        return FailureCategory(score.error_kind)
    if not score.patch_applied:
        return FailureCategory.INVALID_PATCH
    if score.fail_to_pass_passed < len(score.fail_to_pass):
        return FailureCategory.INCORRECT_FIX
    if score.pass_to_pass_regressions:
        return FailureCategory.REGRESSION
    # Tests pass but the run did something an injected instruction asked for.
    return FailureCategory.INCORRECT_FIX


class Harness:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        sandbox: DockerSandbox,
        scorer: Scorer | None = None,
        project_root: Path = Path(),
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._bus = bus
        self._sandbox = sandbox
        self._scorer = scorer or Scorer(
            sandbox, Path(settings.workspace_root), keep_workspaces=settings.keep_workspaces
        )
        self._root = project_root
        self._limits = RepoLimits(
            max_bytes=settings.max_repo_bytes, max_files=settings.max_repo_files
        )

    # ------------------------------------------------------------------ public

    def cassette_for(self, case: Case) -> Path | None:
        return (self._root / case.cassette).resolve() if case.cassette else None

    def runnable(self, dataset: Dataset, options: HarnessOptions) -> tuple[list[Case], list[str]]:
        """Cases to run, and ids skipped because the scripted model has no cassette."""
        cases = [dataset.case(case_id) for case_id in options.case_ids] or list(dataset.cases)
        scripted = load_pricing(self._settings).price(options.model).provider == "scripted"
        if not scripted:
            return cases, []
        runnable = [c for c in cases if (p := self.cassette_for(c)) is not None and p.is_file()]
        skipped = [c.id for c in cases if c not in runnable]
        return runnable, skipped

    async def run(self, dataset: Dataset, options: HarnessOptions) -> UUID:
        cases, skipped = self.runnable(dataset, options)
        if not cases:
            raise DatasetError("no runnable cases (the scripted model needs a recorded cassette)")
        dataset_id, case_rows = await self._sync_dataset(dataset)
        eval_run_id = await self._start(dataset_id, dataset, options, skipped)
        log = logger.bind(eval_run=str(eval_run_id), dataset=dataset.name)
        log.info("eval_started", cases=len(cases), repeats=options.repeats, skipped=skipped)

        semaphore = asyncio.Semaphore(options.concurrency)

        async def one(case: Case, repeat: int) -> CaseOutcome:
            async with semaphore:
                return await self._run_case(
                    eval_run_id=eval_run_id,
                    case_row_id=case_rows[case.id],
                    dataset=dataset,
                    case=case,
                    repeat=repeat,
                    options=options,
                )

        status = EvalRunStatus.COMPLETED
        try:
            outcomes = await asyncio.gather(
                *(one(case, r) for case in cases for r in range(options.repeats))
            )
        except BaseException:
            status = EvalRunStatus.FAILED
            raise
        finally:
            await self._finish(eval_run_id, status)
        log.info(
            "eval_finished",
            resolved=sum(o.resolved for o in outcomes),
            total=len(outcomes),
        )
        return eval_run_id

    # ------------------------------------------------------------------ bookkeeping

    async def _sync_dataset(self, dataset: Dataset) -> tuple[UUID, dict[str, UUID]]:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(EvalDataset).where(
                    EvalDataset.name == dataset.name, EvalDataset.version == dataset.version
                )
            )
            if row is not None and row.content_sha256 != dataset.content_sha256:
                raise DatasetError(
                    f"dataset {dataset.name} v{dataset.version} changed since it was first run; "
                    "bump its version so earlier results stay comparable"
                )
            if row is None:
                row = EvalDataset(
                    name=dataset.name,
                    version=dataset.version,
                    description=dataset.description,
                    source_path=str(dataset.path),
                    content_sha256=dataset.content_sha256,
                )
                session.add(row)
                await session.flush()
            existing = {
                c.case_key: c
                for c in await session.scalars(
                    select(EvalCase).where(EvalCase.dataset_id == row.id)
                )
            }
            for case in dataset.cases:
                if case.id not in existing:
                    existing[case.id] = EvalCase(
                        dataset_id=row.id,
                        case_key=case.id,
                        language=case.language,
                        framework=case.framework,
                        difficulty=case.difficulty,
                        spec=case.model_dump(mode="json"),
                    )
                    session.add(existing[case.id])
            await session.flush()
            return row.id, {key: case.id for key, case in existing.items()}

    async def _start(
        self, dataset_id: UUID, dataset: Dataset, options: HarnessOptions, skipped: list[str]
    ) -> UUID:
        async with self._sessions() as session, session.begin():
            run = EvalRun(
                dataset_id=dataset_id,
                status=EvalRunStatus.RUNNING,
                model=options.model,
                label=options.label,
                config={
                    "budget": options.budget.model_dump(mode="json"),
                    "concurrency": options.concurrency,
                    "cases": list(options.case_ids) or None,
                    "skipped": skipped,
                    "dataset_sha256": dataset.content_sha256,
                    "sandbox_image": self._settings.sandbox_image,
                    "temperature": self._settings.llm_temperature,
                },
                repeats=options.repeats,
                started_at=datetime.now(UTC),
            )
            session.add(run)
            await session.flush()
            return run.id

    async def _finish(self, eval_run_id: UUID, status: EvalRunStatus) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.get(EvalRun, eval_run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = datetime.now(UTC)
            # Prompt versions actually used (from the first agent run that recorded them).
            versions: Sequence[dict[str, Any]] = (
                await session.scalars(
                    select(AgentRun.prompt_versions)
                    .join(EvalResult, EvalResult.agent_run_id == AgentRun.id)
                    .where(EvalResult.eval_run_id == eval_run_id)
                )
            ).all()
            merged: dict[str, Any] = {}
            for item in versions:
                merged.update(item or {})
            run.prompt_versions = merged

    # ------------------------------------------------------------------ one case

    async def _create_agent_run(
        self, eval_run_id: UUID, case: Case, repeat: int, options: HarnessOptions
    ) -> tuple[UUID, Repository]:
        if case.sample is None:
            raise DatasetError(f"{case.id}: only sample repositories are supported")
        samples_root = Path(self._settings.sample_repos_path)
        async with self._sessions() as session:
            repo = await RepositoryService(session).create(
                RepositoryCreate(sample=case.sample), samples_root
            )
        async with self._sessions() as session, session.begin():
            issue = None
            if case.issue_number is not None:
                issue = await session.scalar(
                    select(Issue).where(
                        Issue.repository_id == repo.id, Issue.number == case.issue_number
                    )
                )
            if issue is None:
                issue = Issue(
                    repository_id=repo.id,
                    source=IssueSource.PASTED,
                    number=case.issue_number,
                    title=case.issue_title,
                    body=case.issue_body,
                )
                session.add(issue)
            else:
                issue.title, issue.body = case.issue_title, case.issue_body
            await session.flush()
            run = AgentRun(
                repository_id=repo.id,
                issue_id=issue.id,
                mode=RunMode.AGENT,
                status=RunStatus.QUEUED,
                config={
                    "budget": options.budget.model_dump(mode="json"),
                    "model": options.model,
                    "evaluation": {
                        "eval_run_id": str(eval_run_id),
                        "case_id": case.id,
                        "repeat": repeat,
                        "label": options.label,
                    },
                },
            )
            session.add(run)
            await session.flush()
            seq = await append_event(
                session,
                run.id,
                StatusChanged(
                    from_status=None, to_status=RunStatus.QUEUED, reason="evaluation case"
                ),
            )
            run_id = run.id
        await self._bus.publish(run_id, seq)
        return run_id, repo

    async def _execute(self, run_id: UUID, case: Case) -> None:
        cassette = self.cassette_for(case)
        settings = self._settings
        if cassette is not None:
            settings = settings.model_copy(update={"llm_script_path": str(cassette)})
        recorder = DbRunRecorder(self._sessions, self._bus, run_id)
        try:
            await run_agent(
                run_id=run_id,
                settings=settings,
                session_factory=self._sessions,
                recorder=recorder,
                sandbox=self._sandbox,
            )
        except (LLMError, SandboxError) as exc:
            await mark_run_failed(self._sessions, self._bus, run_id, exc.code, str(exc))
        except RunCancelledError as exc:
            await recorder.close_open_steps(str(exc))
        except Exception as exc:
            logger.exception("eval_case_crashed", run_id=str(run_id), case=case.id)
            await mark_run_failed(
                self._sessions, self._bus, run_id, "internal_error", f"{type(exc).__name__}: {exc}"
            )
            await recorder.close_open_steps("run failed")

    async def _run_case(
        self,
        *,
        eval_run_id: UUID,
        case_row_id: UUID,
        dataset: Dataset,
        case: Case,
        repeat: int,
        options: HarnessOptions,
    ) -> CaseOutcome:
        run_id, repo = await self._create_agent_run(eval_run_id, case, repeat, options)
        started = time.monotonic()
        await self._execute(run_id, case)
        wall_clock_ms = int((time.monotonic() - started) * 1000)

        async with self._sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise DatasetError(f"agent run {run_id} disappeared")
            status, diff, result = run.status, run.final_diff, dict(run.result)
            accounting = (
                run.fix_attempts,
                run.step_count,
                run.input_tokens,
                run.output_tokens,
                run.cost_usd,
            )

        score: Score | None = None
        if status is RunStatus.AWAITING_APPROVAL:
            pull = result.get("pull_request") or {}
            pr_text = f"{pull.get('title', '')}\n{pull.get('body', '')}"
            source = source_for(
                repo,
                samples_root=Path(self._settings.sample_repos_path),
                limits=self._limits,
                clone=CloneConfig(
                    limits=self._limits, timeout_seconds=self._settings.clone_timeout_seconds
                ),
            )
            score = await self._scorer.score(dataset, case, source, diff, pr_text)
        category = categorize(status, result.get("failure"), score)
        resolved = score is not None and score.resolved
        fix_attempts, steps, input_tokens, output_tokens, cost = accounting
        details: dict[str, Any] = {"status": status.value}
        if score is not None:
            details.update(score.details())
        if result.get("failure"):
            details["failure"] = {
                k: result["failure"].get(k) for k in ("code", "message", "category")
            }

        async with self._sessions() as session, session.begin():
            session.add(
                EvalResult(
                    eval_run_id=eval_run_id,
                    eval_case_id=case_row_id,
                    agent_run_id=run_id,
                    repeat_index=repeat,
                    resolved=resolved,
                    patch_applied=bool(score and score.patch_applied),
                    reproduction_created=bool(result.get("reproduction")),
                    fail_to_pass_passed=score.fail_to_pass_passed if score else 0,
                    fail_to_pass_total=len(case.fail_to_pass),
                    pass_to_pass_regressions=score.pass_to_pass_regressions if score else 0,
                    retries=fix_attempts,
                    steps=steps,
                    wall_clock_ms=wall_clock_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=Decimal(cost),
                    failure_category=category,
                    details=details,
                )
            )
        logger.info(
            "eval_case_finished",
            case=case.id,
            repeat=repeat,
            status=status.value,
            resolved=resolved,
            category=category.value if category else None,
        )
        return CaseOutcome(case.id, repeat, run_id, status, resolved, category)
