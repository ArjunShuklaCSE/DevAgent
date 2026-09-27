"""Dependency health probes (Postgres, Redis) and the aggregated health report."""

import asyncio
import time
from collections.abc import Sequence
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.logging_setup import redact_text

logger = structlog.get_logger(__name__)

CheckStatus = Literal["ok", "error"]


class ProbeError(Exception):
    """A dependency is reachable-but-broken or unreachable."""


class HealthProbe(Protocol):
    """A single dependency check. ``check`` returns on success and raises ``ProbeError``."""

    @property
    def name(self) -> str: ...

    async def check(self) -> None: ...


class CheckResult(BaseModel):
    status: CheckStatus
    latency_ms: float
    error: str | None = None


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, CheckResult]


class PostgresProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "database"

    async def check(self) -> None:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            raise ProbeError(f"{type(exc).__name__}: {exc}") from exc


class RedisProbe:
    def __init__(self, client: "Redis") -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "redis"

    async def check(self) -> None:
        try:
            pong = await self._client.ping()
        except (RedisError, OSError) as exc:
            raise ProbeError(f"{type(exc).__name__}: {exc}") from exc
        if pong is not True:
            raise ProbeError(f"unexpected PING reply: {pong!r}")


class HealthService:
    """Runs all probes concurrently, each bounded by ``timeout_seconds``."""

    def __init__(self, probes: Sequence[HealthProbe], version: str, timeout_seconds: float) -> None:
        self._probes = tuple(probes)
        self._version = version
        self._timeout = timeout_seconds

    async def _run(self, probe: HealthProbe) -> CheckResult:
        started = time.perf_counter()
        error: str | None = None
        try:
            async with asyncio.timeout(self._timeout):
                await probe.check()
        except ProbeError as exc:
            error = redact_text(str(exc))[:300]
        except TimeoutError:
            error = f"timed out after {self._timeout:g}s"
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if error is not None:
            logger.warning("health_check_failed", dependency=probe.name, error=error)
            return CheckResult(status="error", latency_ms=latency_ms, error=error)
        return CheckResult(status="ok", latency_ms=latency_ms)

    async def report(self) -> HealthReport:
        results = await asyncio.gather(*(self._run(p) for p in self._probes))
        checks = {p.name: r for p, r in zip(self._probes, results, strict=True)}
        healthy = all(r.status == "ok" for r in checks.values())
        return HealthReport(
            status="ok" if healthy else "degraded", version=self._version, checks=checks
        )
