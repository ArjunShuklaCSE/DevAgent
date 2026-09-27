"""Worker entrypoint: ``python -m backend.worker`` runs it, ``--check`` is the healthcheck.

arq's own health check reads a key the running worker refreshes in Redis every
``health_check_interval`` seconds, so ``--check`` fails if the worker is stuck or gone.
"""

import argparse
import sys
from typing import Any, ClassVar

import structlog
from arq import run_worker
from arq.connections import RedisSettings
from arq.worker import check_health

from backend import __version__
from backend.config import Settings, get_settings
from backend.logging_setup import configure_logging
from backend.worker.jobs import ping

logger = structlog.get_logger(__name__)

QUEUE_NAME = "devagent:queue"
HEALTH_CHECK_INTERVAL_SECONDS = 10


async def _on_startup(_ctx: dict[str, Any]) -> None:
    logger.info("worker_started", version=__version__, queue=QUEUE_NAME)


async def _on_shutdown(_ctx: dict[str, Any]) -> None:
    logger.info("worker_stopped")


def build_worker_settings(settings: Settings) -> type:
    """Build the arq settings class from typed settings (arq expects a class)."""

    class WorkerSettings:
        functions: ClassVar[list[Any]] = [ping]
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
