"""Persist sandbox test runs (``test_runs`` + ``test_results``) and emit ``test_result``."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunRecorder, StepHandle, TestRunKind
from core.events import TestResult as TestResultEvent
from database.models import TestOutcome, TestResult
from database.models import TestRun as TestRunRecord
from database.models import TestRunKind as DbTestRunKind
from sandbox.docker_sandbox import TestRun


class DbTestRunSink:
    __test__ = False

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        events: RunRecorder | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._events = events

    async def record(self, kind: TestRunKind, run: TestRun, step: StepHandle | None) -> uuid.UUID:
        report = run.report
        record = TestRunRecord(
            id=uuid.uuid4(),
            run_id=self._run_id,
            step_id=step.id if step else None,
            kind=DbTestRunKind(kind),
            command=list(run.result.argv),
            exit_code=run.result.exit_code,
            timed_out=run.result.timed_out,
            duration_ms=int(run.result.duration_seconds * 1000),
            passed=report.passed if report else 0,
            failed=report.failed if report else 0,
            errors=report.errors if report else 0,
            skipped=report.skipped if report else 0,
        )
        async with self._session_factory() as session, session.begin():
            session.add(record)
            await session.flush()
            for case in report.cases if report else []:
                session.add(
                    TestResult(
                        test_run_id=record.id,
                        node_id=case.test_id[:1000],
                        outcome=TestOutcome(case.outcome),
                        duration_ms=int(case.duration_seconds * 1000),
                        message=case.message,
                    )
                )
        if self._events is not None:
            await self._events.emit(
                TestResultEvent(
                    test_run_id=record.id,
                    passed=record.passed,
                    failed=record.failed,
                    errors=record.errors,
                    skipped=record.skipped,
                ),
                step,
            )
        return record.id
