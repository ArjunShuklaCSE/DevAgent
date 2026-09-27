from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.api.deps import (
    EventBusDep,
    RequiredUserDep,
    RunServiceDep,
    SessionFactoryDep,
    require_user,
)
from backend.config import Settings
from backend.errors import AppError, NotFoundError
from backend.schemas import (
    ApprovalOut,
    ApprovalRequest,
    CancelRequest,
    DiffOut,
    LlmCallOut,
    Page,
    PullRequestOut,
    RejectRequest,
    RunCreate,
    RunOut,
    RunSummary,
    RunTraceOut,
    StepOut,
    TestRunOut,
    ToolCallOut,
    TraceEventOut,
)
from backend.services.event_store import SqlEventReader
from backend.services.run_details import RunDetails
from backend.sse import InvalidLastEventIdError, parse_last_event_id, stream_run_events
from core.run_status import RunStatus
from database.models import ApprovalDecision

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_user)])


class InvalidLastEventIdAppError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_last_event_id"


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(data: RunCreate, service: RunServiceDep) -> RunOut:
    """Create a run and queue it for the worker."""
    return RunOut.model_validate(await service.create(data))


@router.get("", response_model=Page[RunSummary])
async def list_runs(
    service: RunServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    repository_id: UUID | None = None,
) -> Page[RunSummary]:
    result = await service.list_page(limit, offset, status_filter, repository_id)
    return Page(
        items=[RunSummary.model_validate(r) for r in result.items],
        total=result.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID, service: RunServiceDep) -> RunOut:
    return RunOut.model_validate(await service.get(run_id))


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: UUID, service: RunServiceDep, body: CancelRequest | None = None
) -> RunOut:
    """Cancel a run that has not finished. Returns 409 if it already reached a final state."""
    return RunOut.model_validate(await service.cancel(run_id, body.reason if body else None))


@router.post("/{run_id}/approve", response_model=RunOut)
async def approve_run(
    run_id: UUID, body: ApprovalRequest, service: RunServiceDep, user: RequiredUserDep
) -> RunOut:
    """Approve the run's diff. ``diff_sha256`` must match the diff shown for review
    (409 ``stale_diff`` otherwise). Approval queues the draft PR (or the patch fallback)."""
    run = await service.decide(
        run_id,
        ApprovalDecision.APPROVED,
        body.comment,
        body.diff_sha256,
        user_id=user.id if user is not None else None,
    )
    return RunOut.model_validate(run)


@router.post("/{run_id}/publish", response_model=RunOut)
async def retry_publish(run_id: UUID, service: RunServiceDep) -> RunOut:
    """Try opening the draft PR again (after signing in, a rate limit, or fixing access)."""
    return RunOut.model_validate(await service.retry_publish(run_id))


@router.get("/{run_id}/pull-request", response_model=PullRequestOut)
async def get_pull_request(run_id: UUID, factory: SessionFactoryDep) -> PullRequestOut:
    return PullRequestOut.model_validate(await RunDetails(factory).pull_request(run_id))


@router.get(
    "/{run_id}/patch",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/x-diff": {}}}},
)
async def download_patch(run_id: UUID, factory: SessionFactoryDep, request: Request) -> Response:
    """The final diff as a ``git am`` patch (the fallback when no PR can be opened)."""
    settings: Settings = request.app.state.settings
    filename, patch = await RunDetails(factory).patch(
        run_id, settings.github_commit_name, settings.github_commit_email
    )
    return Response(
        patch,
        media_type="text/x-diff; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{run_id}/reject", response_model=RunOut)
async def reject_run(
    run_id: UUID,
    service: RunServiceDep,
    user: RequiredUserDep,
    body: RejectRequest | None = None,
) -> RunOut:
    run = await service.decide(
        run_id,
        ApprovalDecision.REJECTED,
        body.comment if body else None,
        user_id=user.id if user is not None else None,
    )
    return RunOut.model_validate(run)


@router.get("/{run_id}/tool-calls", response_model=list[ToolCallOut])
async def list_tool_calls(run_id: UUID, factory: SessionFactoryDep) -> list[ToolCallOut]:
    return [ToolCallOut.model_validate(t) for t in await RunDetails(factory).tool_calls(run_id)]


@router.get("/{run_id}/llm-calls", response_model=list[LlmCallOut])
async def list_llm_calls(run_id: UUID, factory: SessionFactoryDep) -> list[LlmCallOut]:
    return [LlmCallOut.model_validate(c) for c in await RunDetails(factory).llm_calls(run_id)]


@router.get("/{run_id}/test-runs", response_model=list[TestRunOut])
async def list_test_runs(run_id: UUID, factory: SessionFactoryDep) -> list[TestRunOut]:
    return [TestRunOut.model_validate(t) for t in await RunDetails(factory).test_runs(run_id)]


@router.get("/{run_id}/diff", response_model=DiffOut)
async def get_diff(run_id: UUID, factory: SessionFactoryDep) -> DiffOut:
    """The final diff, its SHA-256 (what approval binds to), review flags and validation."""
    return DiffOut.model_validate(await RunDetails(factory).diff(run_id))


@router.get("/{run_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(run_id: UUID, factory: SessionFactoryDep) -> list[ApprovalOut]:
    return [ApprovalOut.model_validate(a) for a in await RunDetails(factory).approvals(run_id)]


TRACE_EVENT_LIMIT = 50_000


@router.get("/{run_id}/trace", response_model=RunTraceOut)
async def export_trace(
    run_id: UUID, service: RunServiceDep, factory: SessionFactoryDep, response: Response
) -> RunTraceOut:
    """The whole run as one JSON document: config, steps, events, tool and model calls,
    test results, diff and decisions. Served as a download."""
    run = RunOut.model_validate(await service.get(run_id))
    details = RunDetails(factory)
    page = await SqlEventReader(factory).read_after(run_id, 0, TRACE_EVENT_LIMIT + 1)
    events = page.events if page is not None else []
    response.headers["content-disposition"] = f'attachment; filename="devagent-run-{run_id}.json"'
    return RunTraceOut(
        exported_at=datetime.now(UTC),
        run=run,
        steps=[StepOut.model_validate(s) for s in await service.steps(run_id)],
        events=[
            TraceEventOut(
                seq=e.seq,
                step_id=e.step_id,
                type=e.event_type,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in events[:TRACE_EVENT_LIMIT]
        ],
        events_truncated=len(events) > TRACE_EVENT_LIMIT,
        tool_calls=[ToolCallOut.model_validate(t) for t in await details.tool_calls(run_id)],
        llm_calls=[LlmCallOut.model_validate(c) for c in await details.llm_calls(run_id)],
        test_runs=[TestRunOut.model_validate(t) for t in await details.test_runs(run_id)],
        diff=DiffOut.model_validate(await details.diff(run_id)),
        approvals=[ApprovalOut.model_validate(a) for a in await details.approvals(run_id)],
    )


@router.get("/{run_id}/steps", response_model=list[StepOut])
async def list_steps(run_id: UUID, service: RunServiceDep) -> list[StepOut]:
    return [StepOut.model_validate(s) for s in await service.steps(run_id)]


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(
    *,
    run_id: UUID,
    request: Request,
    session_factory: SessionFactoryDep,
    bus: EventBusDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int | None, Query(ge=0, description="Resume after this seq")] = None,
) -> StreamingResponse:
    """SSE stream of run events. Reconnect with ``Last-Event-ID`` to resume without gaps."""
    try:
        after_seq = parse_last_event_id(last_event_id, after)
    except InvalidLastEventIdError as exc:
        raise InvalidLastEventIdAppError(str(exc)) from exc
    reader = SqlEventReader(session_factory)
    if await reader.read_after(run_id, after_seq, 0) is None:
        raise NotFoundError("Run not found", {"run_id": str(run_id)})
    keepalive = float(request.app.state.sse_keepalive_seconds)
    return StreamingResponse(
        stream_run_events(
            run_id,
            after_seq,
            reader=reader,
            bus=bus,
            is_disconnected=request.is_disconnected,
            keepalive_seconds=keepalive,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
