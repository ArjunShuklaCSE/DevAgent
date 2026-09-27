"""FastAPI application factory.

Run with ``uvicorn backend.app:create_app --factory``. External resources are created
in the lifespan through an injectable ``ResourceFactory`` so tests can substitute them.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from backend import __version__
from backend.api import health
from backend.config import Settings, get_settings
from backend.errors import register_error_handlers
from backend.health import HealthProbe, HealthService, PostgresProbe, RedisProbe
from backend.logging_setup import configure_logging
from backend.middleware import RequestContextMiddleware
from database.engine import create_engine

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AppResources:
    probes: Sequence[HealthProbe]


ResourceFactory = Callable[[Settings], AbstractAsyncContextManager[AppResources]]


@asynccontextmanager
async def default_resources(settings: Settings) -> AsyncIterator[AppResources]:
    """Create the Postgres engine and Redis client, and dispose them on shutdown."""
    engine = create_engine(settings.database_url.get_secret_value())
    redis: Redis = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        yield AppResources(probes=[PostgresProbe(engine), RedisProbe(redis)])
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app(
    settings: Settings | None = None, resource_factory: ResourceFactory = default_resources
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with resource_factory(settings) as resources:
            app.state.health_service = HealthService(
                resources.probes, __version__, settings.health_check_timeout_seconds
            )
            logger.info("api_started", environment=settings.environment, version=__version__)
            yield
        logger.info("api_stopped")

    app = FastAPI(
        title="DevAgent API",
        version=__version__,
        description="Autonomous GitHub issue solver.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
