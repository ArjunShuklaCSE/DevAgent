import asyncio

from backend.config import Settings
from backend.health import HealthService
from tests.conftest import StaticProbe, client_for


class HangingProbe:
    @property
    def name(self) -> str:
        return "slow"

    async def check(self) -> None:
        await asyncio.sleep(10)


async def test_all_probes_ok() -> None:
    service = HealthService([StaticProbe("database"), StaticProbe("redis")], "1.0", 1.0)
    report = await service.report()
    assert report.status == "ok"
    assert set(report.checks) == {"database", "redis"}
    assert all(c.status == "ok" and c.error is None for c in report.checks.values())


async def test_failing_probe_degrades_report() -> None:
    service = HealthService(
        [StaticProbe("database", fail_with="connection refused"), StaticProbe("redis")], "1.0", 1.0
    )
    report = await service.report()
    assert report.status == "degraded"
    assert report.checks["database"].status == "error"
    assert report.checks["database"].error == "connection refused"
    assert report.checks["redis"].status == "ok"


async def test_hanging_probe_times_out() -> None:
    service = HealthService([HangingProbe()], "1.0", 0.05)
    report = await service.report()
    assert report.checks["slow"].status == "error"
    assert report.checks["slow"].error == "timed out after 0.05s"


async def test_probe_errors_are_redacted() -> None:
    probe = StaticProbe("database", fail_with="cannot connect postgresql://app:s3cret@db:5432/x")
    report = await HealthService([probe], "1.0", 1.0).report()
    error = report.checks["database"].error
    assert error is not None
    assert "s3cret" not in error


async def test_health_endpoint_returns_200_when_healthy(settings: Settings) -> None:
    async with client_for(settings, [StaticProbe("database"), StaticProbe("redis")]) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


async def test_health_endpoint_returns_503_when_dependency_down(settings: Settings) -> None:
    probes = [StaticProbe("database"), StaticProbe("redis", fail_with="ConnectionError")]
    async with client_for(settings, probes) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["error"] == "ConnectionError"


async def test_liveness_does_not_touch_dependencies(settings: Settings) -> None:
    probes = [StaticProbe("database", fail_with="down")]
    async with client_for(settings, probes) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
