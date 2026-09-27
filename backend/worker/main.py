"""Worker entrypoint: ``python -m backend.worker`` runs it, ``--check`` is the healthcheck.

arq's own health check reads a key the running worker refreshes in Redis every
``health_check_interval`` seconds, so ``--check`` fails if the worker is stuck or gone.
"""

import argparse
import sys
from typing import Any, ClassVar

import structlog
from arq import cron, run_worker
from arq.connections import RedisSettings
from arq.worker import check_health
from docker.errors import DockerException
from requests.exceptions import RequestException

from backend import __version__
from backend.config import Settings, get_settings
from backend.event_bus import RedisEventBus
from backend.logging_setup import configure_logging
from backend.queue import QUEUE_NAME
from backend.sandbox_factory import build_sandbox
from backend.worker.jobs import execute_run, ping
from backend.worker.sandbox_jobs import reap_sandboxes, sandbox_check
from database.engine import create_engine, create_session_factory
from sandbox.docker_sandbox import SandboxError

logger = structlog.get_logger(__name__)


HEALTH_CHECK_INTERVAL_SECONDS = 10


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    # arq's own connection (ctx["redis"]) is a redis.asyncio.Redis; reuse it for events.
    ctx["event_bus"] = RedisEventBus(ctx["redis"])
    ctx["sandbox"] = None
    ctx["sandbox_image_id"] = None
    try:
        sandbox = build_sandbox(settings)
        ctx["sandbox"] = sandbox
        ctx["sandbox_image_id"] = await sandbox.image_id()
        reaped = await sandbox.reap(settings.sandbox_reap_after_seconds)
    except (SandboxError, DockerException, RequestException) as exc:
        # The worker still serves jobs that need no sandbox; sandbox jobs fail with a
        # clear error until Docker and the image are available.
        logger.warning("sandbox_unavailable", error=str(exc))
    else:
        logger.info("sandbox_ready", image=settings.sandbox_image, reaped=reaped)
    logger.info("worker_started", version=__version__, queue=QUEUE_NAME)


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    sandbox = ctx.get("sandbox")
    if sandbox is not None:
        sandbox.close()
    logger.info("worker_stopped")


def build_worker_settings(settings: Settings) -> type:
    """Build the arq settings class from typed settings (arq expects a class)."""

    class WorkerSettings:
        functions: ClassVar[list[Any]] = [ping, execute_run, sandbox_check]
        cron_jobs: ClassVar[list[Any]] = [cron(reap_sandboxes, minute={0, 15, 30, 45})]
        queue_name = QUEUE_NAME
        redis_settings = RedisSettings.from_dsn(settings.redis_url.get_secret_value())
        health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
        on_startup = _on_startup
        on_shutdown = _on_shutdown
        # Agent runs are long; per-job limits are enforced by the run budget (Phase 5).
        job_timeout = 60 * 60
        max_jobs = 4

    return WorkerSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devagent-worker")
    parser.add_argument("--check", action="store_true", help="exit 0 if the worker is healthy")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    worker_settings = build_worker_settings(settings)
    if args.check:
        return int(check_health(worker_settings))
    run_worker(worker_settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
