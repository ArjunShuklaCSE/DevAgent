"""Support for agent tests: a tiny buggy repository, scripted model turns, test doubles.

``HostPytestSandbox`` runs pytest in a subprocess on the test machine instead of in a
container. It only ever runs the fixture repository below (code written by these
tests), so the orchestrator's logic can be tested with real test results and no
Docker. Runs against real sandboxes are integration tests.
"""

import asyncio
import os
import sys
import time
import uuid
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from agent.components.runtime import ComponentRuntime, ModelSettings
from agent.orchestrator import AgentConfig, IssueSpec, StateMachineOrchestrator
from agent.ports import SourceCheckout, StepHandle, TestRunKind
from llm.budget import BudgetLimits, BudgetTracker
from llm.metered import MeteredLLMClient
from llm.pricing import ModelPrice, PricingTable
from llm.prompting import PromptLibrary
from llm.scripted import ScriptedLLM, ScriptedToolCall, ScriptStep
from sandbox.docker_sandbox import CommandResult, RunWorkspace, TestRun
from sandbox.junit import JUnitError, parse_junit_file
from sandbox.policy import Profile
from tests.fakes import FakeRecorder
from tools.defaults import default_registry
from workspace.clone import copy_local_repository
from workspace.limits import RepoLimits

ROOT = Path(__file__).parents[1]
PROMPTS = ROOT / "agent" / "prompts"
MODEL = "scripted"
PRICING = PricingTable(
    currency="USD",
    models={
        MODEL: ModelPrice(
            provider="scripted", input_per_mtok=Decimal(1), output_per_mtok=Decimal(2)
        )
    },
)

BUGGY_MEAN = '''"""Small statistics helpers."""


def mean(values: list[float]) -> float:
    """Arithmetic mean; the mean of no values is 0.0."""
    return sum(values) / len(values)
'''

REPO_FILES = {
    "pytest.ini": "[pytest]\ntestpaths = tests\n",
    "requirements.txt": "pytest\n",
    "calc/__init__.py": "from calc.stats import mean\n\n__all__ = ['mean']\n",
    "calc/stats.py": BUGGY_MEAN,
    "tests/__init__.py": "",
    "tests/test_stats.py": (
        "from calc import mean\n\n\n"
        "def test_mean():\n    assert mean([1.0, 2.0, 3.0]) == 2.0\n\n\n"
        "def test_single():\n    assert mean([4.0]) == 4.0\n"
    ),
}

ISSUE = IssueSpec(
    title="mean() crashes on an empty list",
    body="`calc.mean([])` raises ZeroDivisionError. The docstring says it returns 0.0.",
    number=7,
)

REPRO_FILE = "tests/test_mean_empty.py"
REPRO_TEST = (
    "from calc import mean\n\n\n"
    "def test_mean_of_empty_list_is_zero():\n    assert mean([]) == 0.0\n"
)
PASSING_REPRO = (
    "from calc import mean\n\n\ndef test_mean_still_works():\n    assert mean([2.0]) == 2.0\n"
)
FIX_OLD = "    return sum(values) / len(values)\n"
FIX_NEW = "    if not values:\n        return 0.0\n    return sum(values) / len(values)\n"
WRONG_FIX = (
    "    if not values:\n        return None  # type: ignore[return-value]\n"
    "    return sum(values) / len(values)\n"
)


def write_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    for name, content in (files or REPO_FILES).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class FixtureSource:
    """A local directory copied into the workspace and committed as the base."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.description = "fixture repository"

    async def checkout(self, dest: Path) -> SourceCheckout:
        result = await copy_local_repository(self._path, dest, RepoLimits())
        return SourceCheckout(result.commit_sha, result.stats.files, result.stats.bytes)


def _result(argv: Sequence[str], code: int, out: str, err: str, seconds: float) -> CommandResult:
    return CommandResult(
        argv=tuple(argv),
        profile="run",
        exit_code=code,
        stdout=out,
        stderr=err,
        stdout_truncated=False,
        stderr_truncated=False,
        duration_seconds=seconds,
    )


class HostPytestSandbox:
    """Runs pytest for the fixture repository in a subprocess (see module docstring)."""

    def __init__(self) -> None:
        self.installed: list[list[str]] = []
        self.test_calls: list[tuple[str, ...]] = []

    async def prepare(self, workspace: RunWorkspace) -> None:
        workspace.env.mkdir(parents=True, exist_ok=True)
        workspace.reports.mkdir(parents=True, exist_ok=True)

    async def image_id(self) -> str:
        return "sha256:host-pytest"

    async def install(
        self, workspace: RunWorkspace, commands: Sequence[Sequence[str]]
    ) -> list[CommandResult]:
        self.installed += [list(c) for c in commands]
        return [_result(c, 0, "", "", 0.0) for c in commands]

    async def run(
        self,
        workspace: RunWorkspace,
        argv: Sequence[str],
        *,
        profile: Profile = "run",
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        return _result(argv, 0, "", "", 0.0)

    async def run_tests(
        self,
        workspace: RunWorkspace,
        test_command: Sequence[str],
        extra_args: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> TestRun:
        self.test_calls.append(tuple(extra_args))
        report_path = workspace.reports / f"junit-{uuid.uuid4().hex}.xml"
        argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args]
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            f"--junitxml={report_path}",
            cwd=workspace.repo,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        result = _result(
            [*test_command, *extra_args],
            process.returncode or 0,
            out.decode(),
            err.decode(),
            time.monotonic() - started,
        )
        try:
            return TestRun(result=result, report=parse_junit_file(report_path))
        except JUnitError as exc:
            return TestRun(result=result, report=None, report_error=str(exc))


class RecordingTestSink:
    __test__ = False

    def __init__(self) -> None:
        self.runs: list[tuple[TestRunKind, TestRun]] = []

    async def record(self, kind: TestRunKind, run: TestRun, step: StepHandle | None) -> UUID:
        self.runs.append((kind, run))
        return uuid.uuid4()


# --------------------------------------------------------------------------- scripts


def submit(component: str, /, **answer: Any) -> ScriptStep:
    return ScriptStep(
        expect_component=component,
        tool_calls=[ScriptedToolCall(name="submit", input=answer)],
        input_tokens=500,
        output_tokens=100,
    )


def tool(component: str, tool_name: str, /, **arguments: Any) -> ScriptStep:
    return ScriptStep(
        expect_component=component,
        tool_calls=[ScriptedToolCall(name=tool_name, input=arguments)],
        input_tokens=400,
        output_tokens=60,
    )


def issue_analysis() -> ScriptStep:
    return submit(
        "issue_analyzer",
        rationale="The issue gives a clear example and the docstring states the contract.",
        summary="mean() divides by zero for an empty list instead of returning 0.0.",
        expected_behavior="mean([]) returns 0.0",
        current_behavior="mean([]) raises ZeroDivisionError",
        reproduction_steps=["from calc import mean", "mean([])"],
        suspected_areas=["calc.mean"],
        acceptance_criteria=["mean([]) == 0.0", "other inputs unchanged"],
        confidence="high",
    )


def localization() -> list[ScriptStep]:
    return [
        tool("localizer", "find_symbol", name="mean"),
        submit(
            "localizer",
            rationale="mean is defined in calc/stats.py and divides by len(values).",
            candidates=[
                {
                    "path": "calc/stats.py",
                    "symbol": "mean",
                    "score": 0.95,
                    "evidence": ["calc/stats.py: return sum(values) / len(values)"],
                },
                {"path": "calc/missing.py", "score": 0.1, "evidence": ["guess"]},
            ],
        ),
    ]


def reproduction(content: str = REPRO_TEST) -> list[ScriptStep]:
    return [
        tool("reproducer", "create_file", path=REPRO_FILE, content=content),
        submit(
            "reproducer",
            rationale="Calling mean([]) shows the ZeroDivisionError from the issue.",
            test_file=REPRO_FILE,
            test_ids=[f"{REPRO_FILE}::test_mean_of_empty_list_is_zero"],
            explanation="mean([]) raises ZeroDivisionError instead of returning 0.0",
        ),
    ]


def plan() -> ScriptStep:
    return submit(
        "planner",
        rationale="Guarding the empty case at the top of mean is the smallest root-cause fix.",
        steps=["In calc/stats.py, mean: return 0.0 when values is empty."],
        files_to_touch=["calc/stats.py"],
        risks=["Callers relying on the exception for empty input."],
        test_strategy="The reproduction test must pass and the existing suite must not regress.",
    )


def edit(new: str = FIX_NEW) -> list[ScriptStep]:
    return [
        tool("editor", "edit_file", path="calc/stats.py", old_str=FIX_OLD, new_str=new),
        submit(
            "editor",
            rationale="Applied the planned guard.",
            changes_made=["mean returns 0.0 for an empty list"],
        ),
    ]


def debug() -> ScriptStep:
    return submit(
        "debugger",
        rationale="The test expects 0.0 but the edit returns None.",
        failure_class="assertion",
        root_cause="The empty-list branch returns None instead of 0.0.",
        next_action="retry_edit",
        guidance="In calc/stats.py, return 0.0 (not None) when values is empty.",
    )


def pr_text() -> ScriptStep:
    return submit(
        "pr_writer",
        rationale="Summarises the validated fix.",
        title="Return 0.0 from mean() for an empty list",
        issue_summary="mean([]) raised ZeroDivisionError although it is documented to return 0.0.",
        root_cause="mean divided by len(values) without handling an empty list.",
        changes_made=["calc/stats.py: return 0.0 when values is empty"],
        tests_added=[f"{REPRO_FILE}: mean([]) returns 0.0"],
        commit_message="Return 0.0 from mean() for an empty list\n\nFixes #7.",
    )


def happy_script() -> list[ScriptStep]:
    return [
        issue_analysis(),
        *localization(),
        *reproduction(),
        plan(),
        *edit(),
        pr_text(),
    ]


# --------------------------------------------------------------------------- harness


class AgentHarness:
    def __init__(
        self,
        tmp_path: Path,
        steps: list[ScriptStep],
        limits: BudgetLimits | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.source_dir = write_repo(tmp_path / "source")
        self.workspace = RunWorkspace.under(tmp_path / "work", "run-1")
        self.scripted = ScriptedLLM(steps)
        self.budget = BudgetTracker(limits or BudgetLimits(), clock=clock)
        self.recorder = FakeRecorder()
        self.tests = RecordingTestSink()
        self.sandbox = HostPytestSandbox()
        self.registry = default_registry()
        client = MeteredLLMClient(self.scripted, PRICING, self.budget, "run-1")
        self.runtime = ComponentRuntime(
            client,
            ModelSettings(model=MODEL, max_output_tokens=1000),
            PromptLibrary(PROMPTS),
            self.registry,
        )
        self.orchestrator = StateMachineOrchestrator(
            config=AgentConfig(run_url="http://localhost:3000/runs/run-1"),
            issue=ISSUE,
            source=FixtureSource(self.source_dir),
            workspace=self.workspace,
            sandbox=self.sandbox,
            runtime=self.runtime,
            budget=self.budget,
            recorder=self.recorder,
            test_sink=self.tests,
        )
