from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest

from backend.app import AppResources, ResourceFactory, create_app
from backend.config import Settings
from backend.event_bus import InMemoryEventBus
from backend.health import HealthProbe, ProbeError
from database.engine import create_engine, create_session_factory


class StaticProbe:
    """Test double for a dependency probe with a fixed outcome."""

    def __init__(self, name: str, *, fail_with: str | None = None) -> None:
        self._name = name
        self._fail_with = fail_with

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> None:
        if self._fail_with is not None:
            raise ProbeError(self._fail_with)


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_format="console", _env_file=None)


class RecordingRunQueue:
    def __init__(self) -> None:
        self.enqueued: list[UUID] = []
        self.published: list[UUID] = []

    async def enqueue_run(self, run_id: UUID) -> bool:
        self.enqueued.append(run_id)
        return True

    async def enqueue_publish(self, run_id: UUID) -> bool:
        self.published.append(run_id)
        return True


def resources_with(probes: Sequence[HealthProbe]) -> ResourceFactory:
    """Resources for tests that never touch the database (the engine connects lazily)."""

    @asynccontextmanager
    async def factory(_settings: Settings) -> AsyncIterator[AppResources]:
        engine = create_engine("postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")
        try:
            yield AppResources(
                probes=probes,
                session_factory=create_session_factory(engine),
                event_bus=InMemoryEventBus(),
                run_queue=RecordingRunQueue(),
            )
        finally:
            await engine.dispose()

    return factory


@asynccontextmanager
async def client_for(
    settings: Settings, probes: Sequence[HealthProbe]
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, resource_factory=resources_with(probes))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
