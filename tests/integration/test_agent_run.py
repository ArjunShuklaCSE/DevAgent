"""Phase 6 acceptance: a ScriptedLLM run on a sample repository reaches
``awaiting_approval`` with a correct diff, through the real sandbox and database.

This drives the worker's composition root (``run_agent``): the repository is copied
from ``sample_repos/slugger``, dependencies are installed and tests run in sandbox
containers, and every step, LLM call, tool call and test run is persisted.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings
from backend.event_bus import InMemoryEventBus
from backend.services.event_store import SqlEventReader
from backend.services.recorder import DbRunRecorder
from backend.worker.agent_job import run_agent
from core.run_status import RunStatus
from database.models import (
    AgentRun,
    AgentStep,
    CodeChange,
    Issue,
    IssueSource,
    LlmCall,
    Repository,
    RepositorySource,
    RunMode,
    ToolCallRecord,
)
from database.models import TestRun as TestRunRow
from database.models import TestRunKind as RunKind
from sandbox.docker_sandbox import DockerSandbox
from tests.integration.test_event_store import session_factory
from tests.sandbox_support import docker_client, sandbox

__all__ = ["docker_client", "sandbox", "session_factory"]  # fixtures

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
CASSETTE = ROOT / "tests" / "cassettes" / "slugger_fix.yaml"
ISSUE_BODY = (
    'slugify("") and slugify("!!!") raise IndexError: list index out of range. '
    "The docstring says empty input gives an empty slug."
)


async def _new_agent_run(factory: async_sessionmaker[AsyncSession]) -> AgentRun:
    async with factory() as session, session.begin():
        repo = Repository(
            source=RepositorySource.LOCAL,
            owner="sample",
            name="slugger",
            clone_url="sample://slugger",
        )
        session.add(repo)
        await session.flush()
        issue = Issue(
            repository_id=repo.id,
            source=IssueSource.PASTED,
            number=3,
            title="slugify crashes on titles without letters",
            body=ISSUE_BODY,
        )
        session.add(issue)
        await session.flush()
        run = AgentRun(
            repository_id=repo.id,
            issue_id=issue.id,
            mode=RunMode.AGENT,
            status=RunStatus.QUEUED,
            config={"budget": {"max_fix_attempts": 2}, "model": "scripted"},
        )
        session.add(run)
    return run


async def test_scripted_agent_run_fixes_the_sample_bug(
    session_factory: async_sessionmaker[AsyncSession], sandbox: DockerSandbox
) -> None:
    run = await _new_agent_run(session_factory)
    settings = Settings(
        _env_file=None,
        workspace_root=str(sandbox.config.workspace_root),
        sample_repos_path=str(ROOT / "sample_repos"),
        prompts_path=str(ROOT / "agent" / "prompts"),
        llm_pricing_path=str(ROOT / "config" / "model_pricing.yaml"),
        llm_script_path=str(CASSETTE),
        keep_workspaces=True,
    )
    recorder = DbRunRecorder(session_factory, InMemoryEventBus(), run.id)

    status = await run_agent(
        run_id=run.id,
        settings=settings,
        session_factory=session_factory,
        recorder=recorder,
        sandbox=sandbox,
    )

    assert status is RunStatus.AWAITING_APPROVAL
    async with session_factory() as session:
        stored = await session.get(AgentRun, run.id)
        assert stored is not None
        assert stored.status is RunStatus.AWAITING_APPROVAL
        diff = stored.final_diff or ""
        steps = list(
            await session.scalars(
                select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
            )
        )
        llm_calls = await session.scalar(
            select(func.count()).select_from(LlmCall).where(LlmCall.run_id == run.id)
        )
        tool_calls = list(
            await session.scalars(select(ToolCallRecord).where(ToolCallRecord.run_id == run.id))
        )
        changes = list(await session.scalars(select(CodeChange).where(CodeChange.run_id == run.id)))
        test_runs = list(
            await session.scalars(
                select(TestRunRow)
                .where(TestRunRow.run_id == run.id)
                .order_by(TestRunRow.created_at)
            )
        )

    # The fix and the reproduction test, and nothing else.
    assert '+    if not words:\n+        return ""\n' in diff
    assert "b/tests/test_slug_empty.py" in diff
    assert diff.count("diff --git") == 2
    assert stored.final_diff_sha256 is not None
    assert len(stored.base_commit_sha or "") == 40
    assert (stored.sandbox_image_digest or "").startswith("sha256:")
    assert set(stored.prompt_versions) == {
        "issue_analyzer",
        "localizer",
        "reproducer",
        "planner",
        "editor",
        "debugger",
        "pr_writer",
    }
    assert stored.config["model"] == "scripted"
    assert stored.fix_attempts == 1
    assert stored.input_tokens > 0

    assert [s.state for s in steps] == [
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
    ]
    assert [s.status.value for s in steps].count("failed") == 1  # the first test step
    assert llm_calls == 14  # every scripted turn was recorded
    assert {t.tool_name for t in tool_calls} == {
        "search_text",
        "read_file",
        "create_file",
        "run_tests",
        "edit_file",
    }
    assert {c.path for c in changes} == {"tests/test_slug_empty.py", "slugger/slug.py"}

    # Reproduction-first, then the retry: repro fails, first fix fails, second passes.
    by_kind = [(t.kind, t.failed + t.errors) for t in test_runs]
    assert by_kind[0] == (RunKind.SUITE, 0)  # baseline: 3 existing tests pass
    assert by_kind[1] == (RunKind.REPRODUCTION, 2)
    assert by_kind[2] == (RunKind.REPRODUCTION, 2)  # the first fix returns "-", not ""
    assert by_kind[-2:] == [(RunKind.REPRODUCTION, 0), (RunKind.SUITE, 0)]
    assert test_runs[-1].passed == 5

    pull_request = stored.result["pull_request"]
    assert pull_request["title"] == "Return an empty slug for titles without letters or digits"
    assert "Fixes #3." in pull_request["body"]
    assert stored.result["validation"]["checks"][1]["status"] == "passed"

    page = await SqlEventReader(session_factory).read_after(run.id, 0, 1000)
    assert page is not None
    types = {e.event_type for e in page.events}
    assert {
        "status_changed",
        "step_started",
        "step_completed",
        "llm_usage",
        "tool_call",
        "test_result",
        "command_output",
    } <= types
