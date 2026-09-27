"""Run queue: how the API hands runs to the worker."""

from typing import Protocol
from uuid import UUID

from arq import ArqRedis

QUEUE_NAME = "devagent:queue"
EXECUTE_RUN_JOB = "execute_run"


class RunQueue(Protocol):
    async def enqueue_run(self, run_id: UUID) -> bool:
        """Queue the run. Returns False if a job for this run already exists."""
        ...


class ArqRunQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue_run(self, run_id: UUID) -> bool:
        # The run id doubles as the job id, so a duplicate enqueue is a no-op.
        job = await self._pool.enqueue_job(
            EXECUTE_RUN_JOB, str(run_id), _job_id=f"run:{run_id}", _queue_name=QUEUE_NAME
        )
        return job is not None
