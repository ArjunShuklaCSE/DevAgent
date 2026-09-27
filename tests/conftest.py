from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
import pytest

from backend.app import AppResources, ResourceFactory, create_app
from backend.config import Settings
from backend.health import HealthProbe, ProbeError


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


def resources_with(probes: Sequence[HealthProbe]) -> ResourceFactory:
    @asynccontextmanager
    async def factory(_settings: Settings) -> AsyncIterator[AppResources]:
        yield AppResources(probes=probes)

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
