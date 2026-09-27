from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from backend.api.deps import EventBusDep, RunServiceDep, SessionFactoryDep
from backend.errors import AppError, NotFoundError
from backend.schemas import CancelRequest, Page, RunCreate, RunOut, RunSummary, StepOut
from backend.services.event_store import SqlEventReader
from backend.sse import InvalidLastEventIdError, parse_last_event_id, stream_run_events
from core.run_status import RunStatus

router = APIRouter(prefix="/runs", tags=["runs"])


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
