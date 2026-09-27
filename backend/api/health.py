"""Health endpoints.

``/health/live`` is liveness (process is serving). ``/health`` is readiness: it checks
Postgres and Redis and returns 503 when any dependency is down.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from backend.health import HealthReport, HealthService

router = APIRouter(tags=["health"])


class LivenessReport(BaseModel):
    status: Literal["ok"] = "ok"


def get_health_service(request: Request) -> HealthService:
    service: HealthService = request.app.state.health_service
    return service


@router.get("/health/live", response_model=LivenessReport)
async def liveness() -> LivenessReport:
    return LivenessReport()


@router.get(
    "/health",
    response_model=HealthReport,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthReport}},
)
async def readiness(
    response: Response, service: Annotated[HealthService, Depends(get_health_service)]
) -> HealthReport:
    report = await service.report()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
