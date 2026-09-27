"""Run state machine (spec Section 4.3): statuses and the allowed transitions."""

from enum import StrEnum
from typing import Final


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLONING = "cloning"
    ANALYZING_REPO = "analyzing_repo"
    ANALYZING_ISSUE = "analyzing_issue"
    LOCALIZING = "localizing"
    REPRODUCING = "reproducing"
    PLANNING = "planning"
    EDITING = "editing"
    TESTING = "testing"
    DEBUGGING = "debugging"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    CREATING_PR = "creating_pr"
    PR_CREATED = "pr_created"
    REJECTED = "rejected"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES: Final = frozenset(
    {
        RunStatus.PR_CREATED,
        RunStatus.REJECTED,
        RunStatus.FAILED,
        RunStatus.BUDGET_EXCEEDED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }
)

# Terminal failures reachable from any non-terminal status.
_FAILURE_EXITS: Final = frozenset(
    {RunStatus.FAILED, RunStatus.BUDGET_EXCEEDED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)

_FORWARD: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.QUEUED: frozenset({RunStatus.CLONING}),
    RunStatus.CLONING: frozenset({RunStatus.ANALYZING_REPO}),
    RunStatus.ANALYZING_REPO: frozenset({RunStatus.ANALYZING_ISSUE}),
    RunStatus.ANALYZING_ISSUE: frozenset({RunStatus.LOCALIZING}),
    RunStatus.LOCALIZING: frozenset({RunStatus.REPRODUCING}),
    RunStatus.REPRODUCING: frozenset({RunStatus.PLANNING}),
    RunStatus.PLANNING: frozenset({RunStatus.EDITING}),
    RunStatus.EDITING: frozenset({RunStatus.TESTING}),
    RunStatus.TESTING: frozenset({RunStatus.DEBUGGING, RunStatus.VALIDATING}),
    RunStatus.DEBUGGING: frozenset({RunStatus.EDITING}),
    RunStatus.VALIDATING: frozenset({RunStatus.AWAITING_APPROVAL}),
    RunStatus.AWAITING_APPROVAL: frozenset({RunStatus.APPROVED, RunStatus.REJECTED}),
    RunStatus.APPROVED: frozenset({RunStatus.CREATING_PR}),
    RunStatus.CREATING_PR: frozenset({RunStatus.PR_CREATED}),
}

# Approval is a human decision; no automatic budget/timeout exit while waiting on it
# except cancellation. Failures while creating the PR are still possible.
_NO_AUTOMATIC_FAILURE: Final = frozenset({RunStatus.AWAITING_APPROVAL})


class InvalidTransitionError(Exception):
    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        super().__init__(f"invalid run transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def allowed_transitions(current: RunStatus) -> frozenset[RunStatus]:
    if current in TERMINAL_STATUSES:
        return frozenset()
    forward = _FORWARD.get(current, frozenset())
    if current in _NO_AUTOMATIC_FAILURE:
        return forward | {RunStatus.CANCELLED}
    return forward | _FAILURE_EXITS


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES


def ensure_transition(current: RunStatus, target: RunStatus) -> None:
    """Raise ``InvalidTransitionError`` unless ``current -> target`` is allowed."""
    if target not in allowed_transitions(current):
        raise InvalidTransitionError(current, target)
