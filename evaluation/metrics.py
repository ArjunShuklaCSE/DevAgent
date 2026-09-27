"""Aggregate stored evaluation results into the numbers the report and dashboard show.

Reads only the database: every number shown anywhere comes from ``eval_results`` rows
written by the harness, never from a constant or an estimate.
"""

from collections import Counter
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AgentRun,
    EvalCase,
    EvalDataset,
    EvalResult,
    EvalRun,
    FailureCategory,
)
from evaluation.stats import Rate, mean, median, wilson


class RateOut(BaseModel):
    k: int
    n: int
    value: float | None
    ci_low: float
    ci_high: float

    @classmethod
    def of(cls, rate: Rate) -> "RateOut":
        return cls(k=rate.k, n=rate.n, value=rate.value, ci_low=rate.low, ci_high=rate.high)

    def __str__(self) -> str:
        return str(Rate(self.k, self.n, self.ci_low, self.ci_high))


class CaseResultOut(BaseModel):
    case_id: str
    difficulty: str
    repeat: int
    agent_run_id: UUID | None
    run_status: str | None
    resolved: bool
    patch_applied: bool
    reproduction_created: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_regressions: int
    retries: int
    steps: int
    wall_clock_ms: int
    tokens: int
    cost_usd: float
    failure_category: FailureCategory | None
    details: dict[str, Any]


class EvalRunSummary(BaseModel):
    id: UUID
    dataset: str
    dataset_version: str
    model: str
    label: str | None
    status: str
    repeats: int
    config: dict[str, Any]
    prompt_versions: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    n: int
    resolved: RateOut
    patch_applied: RateOut
    reproduction_created: RateOut
    regression_free: RateOut
    mean_retries: float | None
    median_steps: float | None
    median_wall_clock_ms: float | None
    median_tokens: float | None
    mean_cost_usd: float | None
    total_cost_usd: float
    failures: dict[str, int]
    by_difficulty: dict[str, RateOut]
    skipped: list[str]


class EvalRunDetail(EvalRunSummary):
    results: list[CaseResultOut]


def _rate(flags: list[bool]) -> RateOut:
    return RateOut.of(wilson(sum(flags), len(flags)))


def summarize(run: EvalRun, dataset: EvalDataset, rows: list[CaseResultOut]) -> EvalRunDetail:
    tokens = [float(r.tokens) for r in rows]
    costs = [r.cost_usd for r in rows]
    patched = [r for r in rows if r.patch_applied]
    difficulties = sorted({r.difficulty for r in rows})
    failures = Counter(r.failure_category.value for r in rows if r.failure_category)
    return EvalRunDetail(
        id=run.id,
        dataset=dataset.name,
        dataset_version=dataset.version,
        model=run.model,
        label=run.label,
        status=run.status.value,
        repeats=run.repeats,
        config=run.config,
        prompt_versions=run.prompt_versions,
        started_at=run.started_at,
        finished_at=run.finished_at,
        n=len(rows),
        resolved=_rate([r.resolved for r in rows]),
        patch_applied=_rate([r.patch_applied for r in rows]),
        reproduction_created=_rate([r.reproduction_created for r in rows]),
        # Among runs whose patch applied: no previously passing test broke.
        regression_free=_rate([r.pass_to_pass_regressions == 0 for r in patched]),
        mean_retries=mean([float(r.retries) for r in rows]),
        median_steps=median([float(r.steps) for r in rows]),
        median_wall_clock_ms=median([float(r.wall_clock_ms) for r in rows]),
        median_tokens=median(tokens),
        mean_cost_usd=mean(costs),
        total_cost_usd=round(sum(costs), 6),
        failures=dict(failures.most_common()),
        by_difficulty={
            d: _rate([r.resolved for r in rows if r.difficulty == d]) for d in difficulties
        },
        skipped=list(run.config.get("skipped") or []),
        results=rows,
    )


async def load_detail(session: AsyncSession, eval_run_id: UUID) -> EvalRunDetail | None:
    run = await session.get(EvalRun, eval_run_id)
    if run is None:
        return None
    dataset = await session.get(EvalDataset, run.dataset_id)
    if dataset is None:
        return None
    query = (
        select(EvalResult, EvalCase, AgentRun.status)
        .join(EvalCase, EvalCase.id == EvalResult.eval_case_id)
        .outerjoin(AgentRun, AgentRun.id == EvalResult.agent_run_id)
        .where(EvalResult.eval_run_id == eval_run_id)
        .order_by(EvalCase.case_key, EvalResult.repeat_index)
    )
    rows = [
        CaseResultOut(
            case_id=case.case_key,
            difficulty=case.difficulty,
            repeat=result.repeat_index,
            agent_run_id=result.agent_run_id,
            run_status=status.value if status is not None else None,
            resolved=result.resolved,
            patch_applied=result.patch_applied,
            reproduction_created=result.reproduction_created,
            fail_to_pass_passed=result.fail_to_pass_passed,
            fail_to_pass_total=result.fail_to_pass_total,
            pass_to_pass_regressions=result.pass_to_pass_regressions,
            retries=result.retries,
            steps=result.steps,
            wall_clock_ms=result.wall_clock_ms,
            tokens=result.input_tokens + result.output_tokens,
            cost_usd=float(result.cost_usd),
            failure_category=result.failure_category,
            details=result.details,
        )
        for result, case, status in await session.execute(query)
    ]
    return summarize(run, dataset, rows)


async def list_runs(session: AsyncSession, limit: int = 50) -> list[EvalRunSummary]:
    ids = await session.scalars(select(EvalRun.id).order_by(EvalRun.created_at.desc()).limit(limit))
    summaries: list[EvalRunSummary] = []
    for eval_run_id in ids.all():
        detail = await load_detail(session, eval_run_id)
        if detail is not None:
            summaries.append(EvalRunSummary.model_validate(detail.model_dump(exclude={"results"})))
    return summaries


async def count_runs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(EvalRun)) or 0
