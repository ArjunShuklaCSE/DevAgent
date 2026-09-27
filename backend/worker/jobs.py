"""arq job functions."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.dry_run import DryRunExecutor
from agent.ports import RunCancelledError
from backend import __version__
from backend.event_bus import EventBus
from backend.services.recorder import DbRunRecorder
from backend.services.runs import mark_run_failed
from core.run_status import RunStatus
from database.models import AgentRun, RunMode

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


async def execute_run(ctx: dict[str, Any], run_id_str: str) -> str:
    """Execute one run. Idempotent: a run that already left ``queued`` is not re-run."""
    run_id = UUID(run_id_str)
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    bus: EventBus = ctx["event_bus"]
    log = logger.bind(run_id=run_id_str)

    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            log.warning("run_not_found")
            return "missing"
        if run.status is not RunStatus.QUEUED:
            log.info("run_already_started", status=run.status.value)
            return f"skipped:{run.status.value}"
        mode = run.mode
        delay_ms = int(run.config.get("dry_run_step_delay_ms", 250))

    recorder = DbRunRecorder(session_factory, bus, run_id)
    log.info("run_started", mode=mode.value)
    try:
        if mode is RunMode.DRY_RUN:
            await DryRunExecutor(recorder, delay_ms / 1000).run()
        else:
            await mark_run_failed(
                session_factory,
                bus,
                run_id,
                "mode_not_available",
                f"Run mode '{mode.value}' is not available in this version",
            )
            return "failed"
    except RunCancelledError as exc:
        closed = await recorder.close_open_steps(str(exc))
        log.info("run_stopped", reason=str(exc), closed_steps=closed)
        return "cancelled"
    except Exception as exc:
        # Job boundary: any unexpected error must still leave the run in a final state.
        log.exception("run_crashed")
        await mark_run_failed(
            session_factory, bus, run_id, "internal_error", f"{type(exc).__name__}: {exc}"
        )
        await recorder.close_open_steps("run failed")
        raise
    log.info("run_finished")
    return "ok"
