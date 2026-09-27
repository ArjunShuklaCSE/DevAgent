import pytest

from agent.dry_run import DRY_RUN_PATH, LABEL, DryRunExecutor
from agent.ports import RunCancelledError
from core.run_status import RunStatus
from tests.fakes import FakeRecorder


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_walks_state_machine_to_awaiting_approval() -> None:
    recorder = FakeRecorder()
    await DryRunExecutor(recorder, 0, sleep=_no_sleep).run()

    assert recorder.transitions == [*DRY_RUN_PATH, RunStatus.AWAITING_APPROVAL]
    assert recorder.status is RunStatus.AWAITING_APPROVAL
    # Exercises the debug/retry loop at least once.
    assert RunStatus.DEBUGGING in recorder.transitions


async def test_every_step_and_event_is_labeled_synthetic() -> None:
    recorder = FakeRecorder()
    await DryRunExecutor(recorder, 0, sleep=_no_sleep).run()

    assert len(recorder.steps) == len(DRY_RUN_PATH)
    assert all(synthetic for _handle, synthetic in recorder.steps)
    assert all(event.synthetic for event in recorder.events)
    assert all(summary.startswith(LABEL) for _id, _outcome, summary in recorder.finished)
    assert all(outcome == "completed" for _id, outcome, _summary in recorder.finished)


async def test_stops_when_run_is_cancelled() -> None:
    recorder = FakeRecorder(cancel_before=RunStatus.PLANNING)
    with pytest.raises(RunCancelledError):
        await DryRunExecutor(recorder, 0, sleep=_no_sleep).run()
    assert RunStatus.PLANNING not in recorder.transitions
    assert recorder.transitions[-1] is RunStatus.REPRODUCING
