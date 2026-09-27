"""Phase 9: hidden-test scoring and a scripted benchmark run with the debug-loop ablation.

Uses a temporary database and real sandbox containers. The ``scripted`` model has a
recorded cassette for the slugger case only, so the other cases are reported as not run.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings
from backend.event_bus import InMemoryEventBus
from backend.schemas import RunBudget
from backend.services.sources import LocalSampleSource
from core.run_status import RunStatus
from database.models import AgentRun, EvalResult, FailureCategory
from evaluation.dataset import load_dataset
from evaluation.harness import Harness, HarnessOptions
from evaluation.metrics import load_detail
from evaluation.report import render
from evaluation.scoring import Scorer
from sandbox.docker_sandbox import DockerSandbox
from tests.integration.test_event_store import session_factory
from tests.sandbox_support import docker_client, sandbox
from workspace.limits import RepoLimits

__all__ = ["docker_client", "sandbox", "session_factory"]  # fixtures

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
DATASET = load_dataset(ROOT / "evaluation" / "datasets" / "starter.yaml")
SLUGGER = DATASET.case("slugger-empty-title")


def _settings(sandbox: DockerSandbox) -> Settings:
    return Settings(
        _env_file=None,
        workspace_root=str(sandbox.config.workspace_root),
        sample_repos_path=str(ROOT / "sample_repos"),
        prompts_path=str(ROOT / "agent" / "prompts"),
        llm_pricing_path=str(ROOT / "config" / "model_pricing.yaml"),
    )


def _source() -> LocalSampleSource:
    return LocalSampleSource(ROOT / "sample_repos", "slugger", RepoLimits())


async def test_scoring_distinguishes_base_gold_and_broken_patches(sandbox: DockerSandbox) -> None:
    scorer = Scorer(sandbox, sandbox.config.workspace_root)
    assert SLUGGER.gold_patch is not None
    gold = DATASET.file(SLUGGER.gold_patch).read_text()

    base = await scorer.score(DATASET, SLUGGER, _source(), None)
    assert not base.resolved
    assert set(base.fail_to_pass.values()) == {"failed"}
    assert set(base.pass_to_pass.values()) == {"passed"}

    fixed = await scorer.score(DATASET, SLUGGER, _source(), gold)
    assert fixed.resolved, fixed.details()

    # A diff whose context does not match the base (e.g. made against other code).
    stale = "\n".join(
        f"{line} # stale" if line.startswith(" ") else line for line in gold.splitlines()
    )
    broken = await scorer.score(DATASET, SLUGGER, _source(), stale)
    assert not broken.patch_applied
    assert broken.error_kind == "invalid_patch"

    # A patch that deletes a passing test is caught by pass-to-pass: the hidden run still
    # asks for it by id, and a missing test counts as a regression.
    deleting = (
        "diff --git a/tests/test_slug.py b/tests/test_slug.py\n"
        "deleted file mode 100644\n" + "".join(["--- a/tests/test_slug.py\n", "+++ /dev/null\n"])
    )
    original = (ROOT / "sample_repos" / "slugger" / "tests" / "test_slug.py").read_text()
    lines = original.splitlines()
    deleting += f"@@ -1,{len(lines)} +0,0 @@\n" + "".join(f"-{line}\n" for line in lines)
    regressed = await scorer.score(DATASET, SLUGGER, _source(), gold + deleting)
    assert regressed.patch_applied
    assert regressed.pass_to_pass_regressions == len(SLUGGER.pass_to_pass)
    assert not regressed.resolved


async def test_scripted_benchmark_with_and_without_the_debug_loop(
    session_factory: async_sessionmaker[AsyncSession], sandbox: DockerSandbox
) -> None:
    harness = Harness(
        settings=_settings(sandbox),
        session_factory=session_factory,
        bus=InMemoryEventBus(),
        sandbox=sandbox,
        project_root=ROOT,
    )
    full = await harness.run(DATASET, HarnessOptions(model="scripted", label="debug-loop"))
    ablation = await harness.run(
        DATASET,
        HarnessOptions(
            model="scripted",
            label="no-debug-loop",
            case_ids=[SLUGGER.id],
            budget=RunBudget(max_fix_attempts=0),
        ),
    )

    async with session_factory() as session:
        with_loop = await load_detail(session, full)
        without = await load_detail(session, ablation)
        results = list(await session.scalars(select(EvalResult)))
        runs = {
            r.id: r
            for r in await session.scalars(
                select(AgentRun).where(AgentRun.id.in_([r.agent_run_id for r in results]))
            )
        }
    assert with_loop is not None
    assert without is not None

    # Only the case with a recorded cassette ran; the rest are listed, not scored.
    assert with_loop.n == 1
    assert len(with_loop.skipped) == len(DATASET.cases) - 1
    assert (with_loop.resolved.k, with_loop.resolved.n) == (1, 1)
    row = with_loop.results[0]
    assert (row.case_id, row.retries, row.fail_to_pass_passed) == (SLUGGER.id, 1, 2)
    assert row.reproduction_created
    assert row.agent_run_id is not None
    assert runs[row.agent_run_id].status is RunStatus.AWAITING_APPROVAL  # never auto-approved
    assert runs[row.agent_run_id].config["evaluation"]["case_id"] == SLUGGER.id
    assert with_loop.prompt_versions  # recorded from the agent run

    # Without the debug loop the scripted model's first (wrong) fix is final.
    assert (without.resolved.k, without.resolved.n) == (0, 1)
    assert without.results[0].failure_category is FailureCategory.BUDGET_EXCEEDED
    assert without.results[0].retries == 0

    report = render(with_loop, without)
    assert "## Comparison with no-debug-loop" in report
    assert "| Resolved (hidden tests pass, no regressions) | 100% (1/1; 95% CI 21% to 100%) |" in (
        report
    )
