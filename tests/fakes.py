"""In-memory fakes shared by unit tests."""

from typing import Any
from uuid import UUID, uuid4

from agent.ports import RunCancelledError, StepHandle, StepOutcome
from core.events import EventPayload
from core.run_status import RunStatus, ensure_transition, is_terminal


class FakeRecorder:
    """Records calls and enforces the real transition table."""

    def __init__(self, cancel_before: RunStatus | None = None) -> None:
        self.run_id = uuid4()
        self.status = RunStatus.QUEUED
        self.transitions: list[RunStatus] = []
        self.steps: list[tuple[StepHandle, bool]] = []
        self.finished: list[tuple[UUID, StepOutcome, str]] = []
        self.events: list[EventPayload] = []
        self._cancel_before = cancel_before

    async def transition(self, target: RunStatus, reason: str | None = None) -> None:
        if target == self._cancel_before:
            self.status = RunStatus.CANCELLED
        if is_terminal(self.status):
            raise RunCancelledError(self.status.value)
        ensure_transition(self.status, target)
        self.status = target
        self.transitions.append(target)

    async def start_step(self, state: RunStatus, *, synthetic: bool = False) -> StepHandle:
        handle = StepHandle(id=uuid4(), sequence=len(self.steps) + 1, state=state)
        self.steps.append((handle, synthetic))
        return handle

    async def finish_step(
        self,
        step: StepHandle,
        outcome: StepOutcome,
        summary: str,
        output: dict[str, Any] | None = None,
    ) -> None:
        self.finished.append((step.id, outcome, summary))

    async def emit(self, payload: EventPayload, step: StepHandle | None = None) -> None:
        self.events.append(payload)
