from collections import deque

import pytest

from core.run_status import (
    TERMINAL_STATUSES,
    InvalidTransitionError,
    RunStatus,
    allowed_transitions,
    ensure_transition,
    is_terminal,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.QUEUED, RunStatus.CLONING),
        (RunStatus.TESTING, RunStatus.DEBUGGING),
        (RunStatus.DEBUGGING, RunStatus.EDITING),
        (RunStatus.TESTING, RunStatus.VALIDATING),
        (RunStatus.VALIDATING, RunStatus.AWAITING_APPROVAL),
        (RunStatus.AWAITING_APPROVAL, RunStatus.APPROVED),
        (RunStatus.AWAITING_APPROVAL, RunStatus.REJECTED),
        (RunStatus.APPROVED, RunStatus.CREATING_PR),
        (RunStatus.CREATING_PR, RunStatus.PR_CREATED),
        (RunStatus.EDITING, RunStatus.BUDGET_EXCEEDED),
        (RunStatus.CLONING, RunStatus.TIMED_OUT),
        (RunStatus.QUEUED, RunStatus.CANCELLED),
    ],
)
def test_allowed_transitions(current: RunStatus, target: RunStatus) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.QUEUED, RunStatus.EDITING),  # skipping ahead
        (RunStatus.PLANNING, RunStatus.REPRODUCING),  # going back
        (RunStatus.TESTING, RunStatus.AWAITING_APPROVAL),  # skipping validation
        (RunStatus.VALIDATING, RunStatus.CREATING_PR),  # skipping human approval
        (RunStatus.AWAITING_APPROVAL, RunStatus.BUDGET_EXCEEDED),  # no auto-fail while waiting
        (RunStatus.CANCELLED, RunStatus.QUEUED),  # terminal
        (RunStatus.PR_CREATED, RunStatus.FAILED),  # terminal
    ],
)
def test_forbidden_transitions(current: RunStatus, target: RunStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(current, target)


def test_terminal_statuses_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert is_terminal(status)
        assert allowed_transitions(status) == frozenset()


def test_pr_creation_requires_approval() -> None:
    sources = [s for s in RunStatus if RunStatus.CREATING_PR in allowed_transitions(s)]
    assert sources == [RunStatus.APPROVED]
    sources = [s for s in RunStatus if RunStatus.APPROVED in allowed_transitions(s)]
    assert sources == [RunStatus.AWAITING_APPROVAL]


def test_every_status_is_reachable_from_queued() -> None:
    seen = {RunStatus.QUEUED}
    frontier = deque([RunStatus.QUEUED])
    while frontier:
        for nxt in allowed_transitions(frontier.popleft()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(RunStatus)


def test_every_non_terminal_status_can_be_cancelled() -> None:
    for status in RunStatus:
        if not is_terminal(status):
            assert RunStatus.CANCELLED in allowed_transitions(status), status
