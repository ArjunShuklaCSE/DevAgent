"""FastAPI application factory.

Run with ``uvicorn backend.app:create_app --factory``. External resources are created
in the lifespan through an injectable ``ResourceFactory`` so tests can substitute them.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend import __version__
from backend.api import health, repositories, runs
from backend.config import Settings, get_settings
from backend.errors import register_error_handlers
from backend.event_bus import EventBus, RedisEventBus
from backend.health import HealthProbe, HealthService, PostgresProbe, RedisProbe
from backend.logging_setup import configure_logging
from backend.middleware import RequestContextMiddleware
from backend.queue import QUEUE_NAME, ArqRunQueue, RunQueue
from database.engine import create_engine, create_session_factory

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AppResources:
    probes: Sequence[HealthProbe]
    session_factory: async_sessionmaker[AsyncSession]
    event_bus: EventBus
    run_queue: RunQueue


ResourceFactory = Callable[[Settings], AbstractAsyncContextManager[AppResources]]


@asynccontextmanager
async def default_resources(settings: Settings) -> AsyncIterator[AppResources]:
    """Create the Postgres engine, Redis clients and arq pool; dispose them on shutdown."""
    redis_url = settings.redis_url.get_secret_value()
    engine = create_engine(settings.database_url.get_secret_value())
    redis: Redis = Redis.from_url(redis_url)
    arq_pool = await create_pool(RedisSettings.from_dsn(redis_url), default_queue_name=QUEUE_NAME)
    try:
        yield AppResources(
            probes=[PostgresProbe(engine), RedisProbe(redis)],
            session_factory=create_session_factory(engine),
            event_bus=RedisEventBus(redis),
            run_queue=ArqRunQueue(arq_pool),
        )
    finally:
        await arq_pool.aclose()
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
            app.state.session_factory = resources.session_factory
            app.state.event_bus = resources.event_bus
            app.state.run_queue = resources.run_queue
            app.state.sse_keepalive_seconds = settings.sse_keepalive_seconds
            app.state.samples_root = Path(settings.sample_repos_path)
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
        expose_headers=["x-request-id"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(repositories.router)
    api_v1.include_router(runs.router)
    app.include_router(api_v1)
    return app
