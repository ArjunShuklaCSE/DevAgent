"""Interfaces the agent uses to record progress. The backend provides implementations.

Keeping these here (not in ``backend``) lets orchestrators and executors be tested with
in-memory fakes and keeps the ``agent`` package free of web/DB imports.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from core.events import EventPayload
from core.run_status import RunStatus

StepOutcome = Literal["completed", "failed", "skipped"]


class RunCancelledError(Exception):
    """The run left the executor's control (cancelled or finished elsewhere)."""


@dataclass(frozen=True)
class StepHandle:
    id: UUID
    sequence: int
    state: RunStatus


class RunRecorder(Protocol):
    run_id: UUID

    async def transition(self, target: RunStatus, reason: str | None = None) -> None:
        """Move the run to ``target``. Raises ``RunCancelledError`` if it was cancelled."""
        ...

    async def start_step(self, state: RunStatus, *, synthetic: bool = False) -> StepHandle: ...

    async def finish_step(
        self,
        step: StepHandle,
        outcome: StepOutcome,
        summary: str,
        output: dict[str, Any] | None = None,
    ) -> None: ...

    async def emit(self, payload: EventPayload, step: StepHandle | None = None) -> None: ...
