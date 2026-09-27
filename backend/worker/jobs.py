"""arq job functions."""

from datetime import UTC, datetime
from typing import Any

import structlog

from backend import __version__

logger = structlog.get_logger(__name__)


async def ping(ctx: dict[str, Any]) -> dict[str, str]:
    """Round-trip diagnostic: proves API → Redis → worker → result wiring end to end."""
    job_id = str(ctx.get("job_id", ""))
    logger.info("ping_job", job_id=job_id)
    return {
        "status": "ok",
        "worker_version": __version__,
        "job_id": job_id,
        "handled_at": datetime.now(UTC).isoformat(),
    }
