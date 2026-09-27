"""In-memory fakes shared by unit tests."""

from typing import Any
from uuid import UUID, uuid4

from agent.ports import RunCancelledError, RunUpdate, StepHandle, StepOutcome
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
        self.updates: list[RunUpdate] = []
        self.rationales: list[str | None] = []
        self.errors: list[dict[str, Any] | None] = []
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
        *,
        rationale: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        self.finished.append((step.id, outcome, summary))
        self.rationales.append(rationale)
        self.errors.append(error)

    async def emit(self, payload: EventPayload, step: StepHandle | None = None) -> None:
        if is_terminal(self.status):
            raise RunCancelledError(self.status.value)
        self.events.append(payload)

    async def update_run(self, update: RunUpdate) -> None:
        self.updates.append(update)

    def result(self) -> dict[str, Any]:
        """The run's result document, merged like the database recorder does."""
        merged: dict[str, Any] = {}
        for update in self.updates:
            merged.update(update.result or {})
        return merged

    def last(self, name: str) -> Any:
        """The latest non-None value recorded for a ``RunUpdate`` field."""
        values = [getattr(u, name) for u in self.updates if getattr(u, name) is not None]
        return values[-1] if values else None
