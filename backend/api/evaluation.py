"""Stored benchmark results (spec 13.3). Read-only: runs are started with ``devagent eval``."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.api.deps import SessionDep, require_user
from backend.errors import NotFoundError
from evaluation.metrics import EvalRunDetail, EvalRunSummary, list_runs, load_detail

router = APIRouter(prefix="/evaluation", tags=["evaluation"], dependencies=[Depends(require_user)])


@router.get("/runs")
async def evaluation_runs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[EvalRunSummary]:
    return await list_runs(session, limit)


@router.get("/runs/{eval_run_id}")
async def evaluation_run(eval_run_id: UUID, session: SessionDep) -> EvalRunDetail:
    detail = await load_detail(session, eval_run_id)
    if detail is None:
        raise NotFoundError("Evaluation run not found", {"eval_run_id": str(eval_run_id)})
    return detail
