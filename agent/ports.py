"""Interfaces the agent uses to record progress. The backend provides implementations.

Keeping these here (not in ``backend``) lets orchestrators and executors be tested with
in-memory fakes and keeps the ``agent`` package free of web/DB imports.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from core.events import EventPayload
from core.run_status import RunStatus
from sandbox.docker_sandbox import TestRun

StepOutcome = Literal["completed", "failed", "skipped"]
TestRunKind = Literal["reproduction", "suite", "validation", "hidden"]


class RunCancelledError(Exception):
    """The run left the executor's control (cancelled or finished elsewhere)."""


@dataclass(frozen=True)
class StepHandle:
    id: UUID
    sequence: int
    state: RunStatus


@dataclass(frozen=True)
class RunUpdate:
    """Facts about a run recorded as they become known. ``None`` fields are left as is.

    ``config`` and ``result`` keys are merged into the run's config snapshot and result
    document (plan, validation, PR text, ...), so each step can add its part without
    rewriting the others.
    """

    base_commit_sha: str | None = None
    config: dict[str, Any] | None = None
    sandbox_image_digest: str | None = None
    prompt_versions: dict[str, str] | None = None
    final_diff: str | None = None
    final_diff_sha256: str | None = None
    injection_flags: list[dict[str, Any]] | None = None
    fix_attempts: int | None = None
    result: dict[str, Any] | None = None


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
        *,
        rationale: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None: ...

    async def emit(self, payload: EventPayload, step: StepHandle | None = None) -> None: ...

    async def update_run(self, update: RunUpdate) -> None: ...


class TestRunSink(Protocol):
    """Persists sandbox test runs (``test_runs`` and ``test_results``) and emits events."""

    __test__: bool

    async def record(self, kind: TestRunKind, run: TestRun, step: StepHandle | None) -> UUID: ...


@dataclass(frozen=True)
class SourceCheckout:
    commit_sha: str
    files: int
    bytes: int


class RepositorySource(Protocol):
    """Puts the run's repository at ``dest`` (a clone or a local sample copy)."""

    description: str

    async def checkout(self, dest: Path) -> SourceCheckout: ...
