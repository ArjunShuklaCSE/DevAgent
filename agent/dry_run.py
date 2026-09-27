"""Dry run: walks the real state machine with SYNTHETIC steps to exercise the pipeline.

Nothing here clones a repository, runs a command or calls a model. Every step and event
it produces is marked ``synthetic`` and labeled "[dry run]" so it can never be mistaken
for agent output. It exists to prove persistence, the event stream and SSE replay
(spec Phase 1) and remains useful as a smoke test of a deployment.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

from agent.ports import RunRecorder
from core.events import CommandOutput
from core.run_status import RunStatus

LABEL: Final = "[dry run]"

# Happy path including one debug/retry loop, ending where a human would review.
DRY_RUN_PATH: Final[tuple[RunStatus, ...]] = (
    RunStatus.CLONING,
    RunStatus.ANALYZING_REPO,
    RunStatus.ANALYZING_ISSUE,
    RunStatus.LOCALIZING,
    RunStatus.REPRODUCING,
    RunStatus.PLANNING,
    RunStatus.EDITING,
    RunStatus.TESTING,
    RunStatus.DEBUGGING,
    RunStatus.EDITING,
    RunStatus.TESTING,
    RunStatus.VALIDATING,
)


class DryRunExecutor:
    def __init__(
        self,
        recorder: RunRecorder,
        step_delay_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._recorder = recorder
        self._delay = step_delay_seconds
        self._sleep = sleep

    async def run(self) -> None:
        for state in DRY_RUN_PATH:
            await self._recorder.transition(state, reason=f"{LABEL} synthetic transition")
            step = await self._recorder.start_step(state, synthetic=True)
            await self._recorder.emit(
                CommandOutput(
                    stream="stdout",
                    text=f"{LABEL} synthetic output for step '{state.value}'\n",
                    synthetic=True,
                ),
                step,
            )
            await self._sleep(self._delay)
            await self._recorder.finish_step(
                step,
                "completed",
                f"{LABEL} Synthetic step: no repository was cloned, "
                "no command was run and no model was called.",
                {"synthetic": True},
            )
        await self._recorder.transition(
            RunStatus.AWAITING_APPROVAL,
            reason=f"{LABEL} finished; there is no diff to approve. Cancel the run to close it.",
        )
