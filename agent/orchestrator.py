"""The agent loop: an explicit state machine over the components (spec 4.3, 4.4).

``StateMachineOrchestrator`` walks one run from ``cloning`` to ``awaiting_approval``:

    clone -> analyze repo (install, baseline tests) -> analyze issue -> localize
    -> reproduce (a new test that must FAIL before any fix) -> plan
    -> edit -> test -> [debug -> edit -> test]* -> validate -> awaiting_approval

Every state is a recorded step. Budgets are checked before each step, fix attempt and
LLM call; the first limit hit ends the run as ``budget_exceeded`` (``timed_out`` for the
wall clock). Anything else that stops the run ends it as ``failed`` with a stable code
and a failure category (used by the evaluation taxonomy). Cancellation surfaces as
``RunCancelledError`` from the recorder and is left to the caller.

The model never decides what counts as success: the orchestrator runs the tests and
compares them with the baseline itself.
"""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import structlog

from agent.analysis.analyzer import StaticRepositoryAnalyzer
from agent.analysis.model import RepoProfile
from agent.components.runtime import ComponentRuntime, ToolLoopExhaustedError, compact_json
from agent.components.schemas import (
    DebugDiagnosis,
    EditReport,
    IssueAnalysis,
    Localization,
    Plan,
    PullRequestText,
    Reproduction,
)
from agent.ports import (
    RepositorySource,
    RunRecorder,
    RunUpdate,
    StepHandle,
    TestRunKind,
    TestRunSink,
)
from agent.pr_text import render_pr_body
from agent.validation import (
    CheckResult,
    TestComparison,
    ValidationReport,
    changed_paths,
    classify_failure,
    command_check,
    compare_to_baseline,
    failure_digest,
    reproduction_fails,
    reproduction_passes,
    review_flags,
)
from core.events import CommandOutput, ErrorEvent
from core.run_status import RunStatus
from llm.budget import BudgetExceededError, BudgetTracker
from llm.prompting import UntrustedContent
from llm.structured import StructuredOutputError
from llm.types import LLMError
from sandbox.docker_sandbox import CommandResult, RunWorkspace, SandboxError, TestRun
from sandbox.policy import PolicyViolationError
from tools.base import SandboxRunner, ToolContext
from tools.errors import ToolError
from tools.execution import render_command
from tools.git import diff_against_base, git
from tools.paths import WorkspacePaths
from workspace.clone import CloneError
from workspace.limits import LimitExceededError, RepoLimits, measure_tree

logger = structlog.get_logger(__name__)

FailureCategory = Literal[
    "environment",
    "localization",
    "reproduction",
    "incorrect_fix",
    "regression",
    "invalid_patch",
    "budget_exceeded",
    "timeout",
    "infra",
]

READ_TOOLS = [
    "list_tree",
    "search_files",
    "search_text",
    "find_symbol",
    "find_references",
    "read_file",
]
REPRODUCER_TOOLS = [*READ_TOOLS, "create_file", "edit_file", "run_tests"]
EDITOR_TOOLS = [*READ_TOOLS, "edit_file", "create_file", "run_tests", "git_diff"]
EXCERPT_LINES = 250
MAX_EXCERPT_FILES = 3
OUTPUT_EVENT_CHARS = 4000


class AgentSandbox(SandboxRunner, Protocol):
    async def prepare(self, workspace: RunWorkspace) -> None: ...

    async def install(
        self, workspace: RunWorkspace, commands: Sequence[Sequence[str]]
    ) -> list[CommandResult]: ...

    async def image_id(self) -> str: ...


class Orchestrator(Protocol):
    """Runs one agent run to a final or review state."""

    async def run(self) -> RunStatus: ...


class RunFailedError(Exception):
    """The run cannot continue. ``category`` feeds the evaluation failure taxonomy."""

    def __init__(
        self,
        code: str,
        message: str,
        category: FailureCategory,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.details = details or {}


@dataclass(frozen=True)
class IssueSpec:
    title: str
    body: str
    number: int | None = None
    url: str | None = None

    def render(self) -> str:
        return f"# {self.title}\n\n{self.body}".strip()


@dataclass(frozen=True)
class AgentConfig:
    run_url: str
    repo_limits: RepoLimits = field(default_factory=RepoLimits)
    localizer_rounds: int = 12
    reproducer_rounds: int = 10
    editor_rounds: int = 12
    reproduction_attempts: int = 2


@dataclass(frozen=True)
class _Stop:
    status: RunStatus
    code: str
    message: str
    category: FailureCategory
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Baseline:
    tests: TestRun
    checks: dict[str, CommandResult | None]


class StateMachineOrchestrator:
    def __init__(
        self,
        *,
        config: AgentConfig,
        issue: IssueSpec,
        source: RepositorySource,
        workspace: RunWorkspace,
        sandbox: AgentSandbox,
        runtime: ComponentRuntime,
        budget: BudgetTracker,
        recorder: RunRecorder,
        test_sink: TestRunSink,
        analyzer: StaticRepositoryAnalyzer | None = None,
    ) -> None:
        self._config = config
        self._issue = issue
        self._source = source
        self._ws = workspace
        self._sandbox = sandbox
        self._runtime = runtime
        self._budget = budget
        self._recorder = recorder
        self._tests = test_sink
        self._analyzer = analyzer or StaticRepositoryAnalyzer()
        self._step: StepHandle | None = None
        self._base_commit = ""
        self._log = logger.bind(run_id=str(recorder.run_id))

    # ------------------------------------------------------------------ entry point
    async def run(self) -> RunStatus:
        try:
            async with asyncio.timeout(self._budget.remaining_seconds()):
                await self._pipeline()
        except (
            BudgetExceededError,
            TimeoutError,
            RunFailedError,
            StructuredOutputError,
            ToolLoopExhaustedError,
            LLMError,
            SandboxError,
        ) as exc:
            return await self._stop(self._describe(exc))
        return RunStatus.AWAITING_APPROVAL

    def _describe(self, exc: Exception) -> _Stop:
        """Map what stopped the run to its final status, error code and category."""
        if isinstance(exc, BudgetExceededError):
            if exc.limit == "wall_clock":
                return _Stop(RunStatus.TIMED_OUT, "budget_wall_clock", str(exc), "timeout")
            return _Stop(
                RunStatus.BUDGET_EXCEEDED, f"budget_{exc.limit}", str(exc), "budget_exceeded"
            )
        if isinstance(exc, TimeoutError):
            limit = self._budget.limits.wall_clock_seconds
            message = f"run exceeded its wall-clock limit of {limit}s"
            return _Stop(RunStatus.TIMED_OUT, "budget_wall_clock", message, "timeout")
        if isinstance(exc, RunFailedError):
            return _Stop(RunStatus.FAILED, exc.code, str(exc), exc.category, exc.details)
        if isinstance(exc, (StructuredOutputError, ToolLoopExhaustedError)):
            category = _category_for_state(self._step.state if self._step else None)
            return _Stop(RunStatus.FAILED, "invalid_model_output", str(exc), category)
        if isinstance(exc, LLMError):
            code = f"llm_{exc.code}"
        elif isinstance(exc, SandboxError):
            code = exc.code
        else:
            code = "internal_error"
        return _Stop(RunStatus.FAILED, code, str(exc), "infra")

    async def _pipeline(self) -> None:
        await self._clone()
        profile, baseline = await self._analyze_repo()
        analysis = await self._analyze_issue(profile)
        localization = await self._localize(analysis)
        reproduction, repro_run = await self._reproduce(analysis, localization, profile)
        plan = await self._plan(analysis, localization, reproduction, repro_run)
        await self._fix_loop(profile, analysis, plan, reproduction, baseline)
        await self._validate(profile, analysis, plan, reproduction, baseline)

    # ------------------------------------------------------------------ steps
    async def _enter(self, state: RunStatus, reason: str) -> StepHandle:
        self._budget.before_step()
        if state is not RunStatus.CLONING:
            await self._check_workspace_size()
        await self._recorder.transition(state, reason)
        self._step = await self._recorder.start_step(state)
        return self._step

    async def _finish(
        self,
        summary: str,
        output: dict[str, Any] | None = None,
        *,
        rationale: str | None = None,
        outcome: Literal["completed", "failed"] = "completed",
    ) -> None:
        assert self._step is not None  # noqa: S101 - only called inside a step
        step, self._step = self._step, None
        await self._recorder.finish_step(step, outcome, summary, output, rationale=rationale)
        await self._save_progress()

    async def _save_progress(self) -> None:
        await self._recorder.update_run(
            RunUpdate(
                prompt_versions=self._runtime.prompt_versions,
                injection_flags=[
                    {
                        "pattern": f.pattern_id,
                        "label": f.label,
                        "source": f.source,
                        "line": f.line,
                        "excerpt": f.excerpt,
                    }
                    for f in self._runtime.injection_flags
                ],
                fix_attempts=self._budget.fix_attempts,
                result={"budget": self._budget.snapshot()},
            )
        )

    async def _stop(self, stop: _Stop) -> RunStatus:
        status, code, message, details = stop.status, stop.code, stop.message, stop.details
        self._log.info("run_stopping", status=status.value, code=code, message=message)
        error = {"code": code, "message": message, "category": stop.category, **details}
        if self._step is not None:
            step, self._step = self._step, None
            await self._recorder.finish_step(step, "failed", message, error=error)
        diff = await self._current_diff()
        await self._recorder.update_run(
            RunUpdate(
                final_diff=diff,
                final_diff_sha256=_sha256(diff) if diff is not None else None,
                result={"failure": error},
            )
        )
        await self._save_progress()
        await self._recorder.emit(ErrorEvent(code=code, message=message, details=details))
        await self._recorder.transition(status, message)
        return status

    async def _current_diff(self) -> str | None:
        if not self._base_commit:
            return None
        try:
            diff, _stat = await diff_against_base(self._ws.repo, self._base_commit)
        except ToolError:
            return None
        return diff

    async def _check_workspace_size(self) -> None:
        limits = self._config.repo_limits
        try:
            await asyncio.to_thread(measure_tree, self._ws.repo, stop_after=limits)
        except LimitExceededError as exc:
            raise RunFailedError(
                "workspace_too_large", f"workspace grew past its limit: {exc}", "infra"
            ) from exc

    def _ctx(
        self,
        profile: RepoProfile | None = None,
        *,
        read_only: frozenset[str] = frozenset(),
        allowed_protected: frozenset[str] = frozenset(),
    ) -> ToolContext:
        return ToolContext(
            run_id=str(self._recorder.run_id),
            workspace=self._ws,
            base_commit=self._base_commit,
            sandbox=self._sandbox,
            test_command=tuple(profile.test_command) if profile and profile.test_command else None,
            allowed_protected_paths=allowed_protected,
            read_only_paths=read_only,
            command_timeout_seconds=self._command_timeout(),
            step_id=str(self._step.id) if self._step else None,
        )

    def _command_timeout(self) -> float:
        return max(
            1.0,
            min(
                float(self._budget.limits.command_timeout_seconds), self._budget.remaining_seconds()
            ),
        )

    async def _emit_command(self, result: CommandResult) -> None:
        text = render_command(result)
        if len(text) > OUTPUT_EVENT_CHARS:
            text = text[: OUTPUT_EVENT_CHARS // 2] + "\n[...]\n" + text[-OUTPUT_EVENT_CHARS // 2 :]
        stream: Literal["stdout", "stderr"] = "stdout" if result.ok else "stderr"
        await self._recorder.emit(CommandOutput(stream=stream, text=text + "\n"), self._step)

    async def _run_tests(
        self, profile: RepoProfile, kind: TestRunKind, selector: Sequence[str] = ()
    ) -> TestRun:
        assert profile.test_command is not None  # noqa: S101 - checked in analyze_repo
        run = await self._sandbox.run_tests(
            self._ws, profile.test_command, selector, timeout_seconds=self._command_timeout()
        )
        await self._emit_command(run.result)
        await self._tests.record(kind, run, self._step)
        return run

    async def _run_check(self, command: list[str] | None) -> CommandResult | None:
        if command is None:
            return None
        try:
            result = await self._sandbox.run(
                self._ws, command, timeout_seconds=self._command_timeout()
            )
        except PolicyViolationError as exc:
            self._log.warning("check_command_denied", command=command, code=exc.code)
            return None
        await self._emit_command(result)
        return result

    # ------------------------------------------------------------------ 1. clone
    async def _clone(self) -> None:
        await self._enter(RunStatus.CLONING, "checking out the repository")
        try:
            checkout = await self._source.checkout(self._ws.repo)
        except CloneError as exc:
            raise RunFailedError(exc.code, str(exc), "environment") from exc
        self._base_commit = checkout.commit_sha
        await self._sandbox.prepare(self._ws)
        image = await self._sandbox.image_id()
        await self._recorder.update_run(
            RunUpdate(base_commit_sha=checkout.commit_sha, sandbox_image_digest=image[:100])
        )
        await self._finish(
            f"Checked out {self._source.description} at {checkout.commit_sha[:12]} "
            f"({checkout.files} files).",
            {"commit": checkout.commit_sha, "files": checkout.files, "bytes": checkout.bytes},
        )

    # ------------------------------------------------------------------ 2. repository
    async def _analyze_repo(self) -> tuple[RepoProfile, _Baseline]:
        await self._enter(
            RunStatus.ANALYZING_REPO, "detecting how to build and test the repository"
        )
        profile = await asyncio.to_thread(self._analyzer.analyze, self._ws.repo)
        if not profile.supported or profile.test_command is None:
            raise RunFailedError(
                "unsupported_repository",
                profile.unsupported_reason or "no test command was detected",
                "environment",
            )
        self._runtime.flag(self._issue.render(), "issue")
        for path in sorted(self._ws.repo.glob("*.md"))[:5]:
            if path.is_file() and not path.is_symlink():
                self._runtime.flag(path.read_text("utf-8", "replace")[:65536], f"file:{path.name}")

        installs = await self._sandbox.install(self._ws, profile.install_commands)
        for result in installs:
            await self._emit_command(result)
        if not all(r.ok for r in installs):
            failed = next(r for r in installs if not r.ok)
            raise RunFailedError(
                "install_failed",
                f"dependency install failed: {' '.join(failed.argv)}",
                "environment",
                {"output": (failed.stderr or failed.stdout)[-2000:]},
            )
        tests = await self._run_tests(profile, "suite")
        if tests.report is None:
            raise RunFailedError(
                "baseline_tests_did_not_run",
                f"the test suite did not produce a report: {tests.report_error}",
                "environment",
                {"output": (tests.result.stderr or tests.result.stdout)[-2000:]},
            )
        checks = {
            "lint": await self._run_check(profile.lint_command),
            "format": await self._run_check(profile.format_check_command),
            "typecheck": await self._run_check(profile.typecheck_command),
        }
        report = tests.report
        await self._recorder.update_run(
            RunUpdate(result={"repository": profile.model_dump(mode="json")})
        )
        await self._finish(
            f"{profile.primary_language} project, tests with `{' '.join(profile.test_command)}`. "
            f"Baseline: {report.passed} passed, {report.failed} failed, {report.errors} errors.",
            {
                "profile": profile.model_dump(mode="json"),
                "baseline": {
                    "passed": report.passed,
                    "failed": report.failed,
                    "errors": report.errors,
                },
                "checks": {k: (v.exit_code if v else None) for k, v in checks.items()},
            },
        )
        return profile, _Baseline(tests=tests, checks=checks)

    # ------------------------------------------------------------------ 3. issue
    async def _analyze_issue(self, profile: RepoProfile) -> IssueAnalysis:
        step = await self._enter(RunStatus.ANALYZING_ISSUE, "understanding the issue")
        analysis = await self._runtime.structured(
            "issue_analyzer",
            "Analyze the issue below for the repository described in the profile.",
            [
                UntrustedContent(self._issue.render(), "issue"),
                UntrustedContent(_profile_summary(profile), "repo_profile"),
            ],
            IssueAnalysis,
            step_id=str(step.id),
        )
        await self._recorder.update_run(RunUpdate(result={"issue_analysis": analysis.model_dump()}))
        await self._finish(analysis.summary, analysis.model_dump(), rationale=analysis.rationale)
        return analysis

    # ------------------------------------------------------------------ 4. localize
    async def _localize(self, analysis: IssueAnalysis) -> Localization:
        await self._enter(RunStatus.LOCALIZING, "finding the code responsible")
        result = await self._runtime.tool_loop(
            "localizer",
            "Find the code responsible for this issue.\n\n## Issue analysis\n"
            + compact_json(analysis.model_dump(exclude={"rationale"})),
            [UntrustedContent(self._issue.render(), "issue")],
            Localization,
            tools=READ_TOOLS,
            ctx=self._ctx(),
            max_rounds=self._config.localizer_rounds,
        )
        paths = WorkspacePaths(self._ws.repo)
        kept = []
        for candidate in result.value.candidates:
            try:
                if paths.resolve(candidate.path).is_file():
                    kept.append(candidate)
            except ToolError:
                continue
        if not kept:
            raise RunFailedError(
                "localization_failed",
                "none of the suggested locations exist in the repository",
                "localization",
                {"candidates": [c.path for c in result.value.candidates]},
            )
        localization = result.value.model_copy(update={"candidates": kept})
        await self._finish(
            "Top candidates: " + ", ".join(f"{c.path} ({c.score:.2f})" for c in kept[:3]),
            {**localization.model_dump(), "tool_rounds": result.rounds},
            rationale=localization.rationale,
        )
        return localization

    # ------------------------------------------------------------------ 5. reproduce
    async def _reproduce(
        self, analysis: IssueAnalysis, localization: Localization, profile: RepoProfile
    ) -> tuple[Reproduction, TestRun]:
        await self._enter(RunStatus.REPRODUCING, "writing a failing test that shows the bug")
        task = (
            "Write a new test that fails because of this bug.\n\n## Issue analysis\n"
            + compact_json(analysis.model_dump(exclude={"rationale"}))
            + "\n\n## Likely locations\n"
            + compact_json([c.model_dump() for c in localization.candidates])
            + f"\n\nTest directories: {profile.test_directories or ['(none detected)']}"
        )
        feedback = ""
        last: TestRun | None = None
        for attempt in range(1, self._config.reproduction_attempts + 1):
            result = await self._runtime.tool_loop(
                "reproducer",
                task + feedback,
                [UntrustedContent(self._issue.render(), "issue")],
                Reproduction,
                tools=REPRODUCER_TOOLS,
                ctx=self._ctx(profile),
                max_rounds=self._config.reproducer_rounds,
            )
            reproduction = result.value
            problem = await self._only_new_files(reproduction.test_file)
            if problem is None:
                last = await self._run_tests(profile, "reproduction", [reproduction.test_file])
                if last.report is not None and reproduction_fails(last.report):
                    await self._recorder.update_run(
                        RunUpdate(
                            result={
                                "reproduction": {
                                    **reproduction.model_dump(),
                                    "failing_before_fix": [
                                        c.test_id for c in last.report.failing()
                                    ],
                                }
                            }
                        )
                    )
                    await self._finish(
                        f"{reproduction.test_file} fails on the current code "
                        f"({last.report.failed} failing): {reproduction.explanation}",
                        {**reproduction.model_dump(), "attempt": attempt},
                        rationale=reproduction.rationale,
                    )
                    return reproduction, last
                problem = _why_not_failing(last)
            feedback = (
                f"\n\n## Attempt {attempt} was rejected\n{problem}\n"
                "Fix the test (it must fail on the current code because of the bug) and "
                "submit again."
            )
            self._log.info("reproduction_rejected", attempt=attempt, problem=problem)
        raise RunFailedError(
            "reproduction_failed",
            f"could not write a test that fails because of the bug "
            f"in {self._config.reproduction_attempts} attempts",
            "reproduction",
            {"last_output": failure_digest(last) if last else None},
        )

    async def _only_new_files(self, test_file: str) -> str | None:
        """The reproduction step may only add files; undo anything else it changed."""
        diff, _stat = await diff_against_base(self._ws.repo, self._base_commit)
        status = await git(
            self._ws.repo, ["diff", "--cached", "--name-status", "--no-renames", self._base_commit]
        )
        modified = [line.split("\t", 1)[1] for line in status.splitlines() if line[:1] != "A"]
        if modified:
            await git(self._ws.repo, ["checkout", self._base_commit, "--", *modified])
            return (
                "The reproduction step may only create new files; these existing files were "
                f"changed and have been restored: {', '.join(modified)}"
            )
        if test_file not in changed_paths(diff):
            return f"{test_file} was not created. Create it with create_file."
        return None

    # ------------------------------------------------------------------ 6. plan
    async def _plan(
        self,
        analysis: IssueAnalysis,
        localization: Localization,
        reproduction: Reproduction,
        repro_run: TestRun,
    ) -> Plan:
        step = await self._enter(RunStatus.PLANNING, "planning the fix")
        untrusted = [UntrustedContent(self._issue.render(), "issue")]
        untrusted += self._excerpts([c.path for c in localization.candidates[:MAX_EXCERPT_FILES]])
        untrusted += self._excerpts([reproduction.test_file])
        untrusted.append(
            UntrustedContent(failure_digest(repro_run), "test_output", (("tests", "reproduction"),))
        )
        plan = await self._runtime.structured(
            "planner",
            "Plan the fix.\n\n## Issue analysis\n"
            + compact_json(analysis.model_dump(exclude={"rationale"}))
            + "\n\n## Localization\n"
            + compact_json([c.model_dump() for c in localization.candidates])
            + f"\n\n## Reproduction test (read-only)\n{reproduction.test_file}: "
            + reproduction.explanation,
            untrusted,
            Plan,
            step_id=str(step.id),
        )
        await self._recorder.update_run(RunUpdate(result={"plan": plan.model_dump()}))
        await self._finish(
            f"{len(plan.steps)} steps touching {', '.join(plan.files_to_touch)}.",
            plan.model_dump(),
            rationale=plan.rationale,
        )
        return plan

    def _excerpts(self, rel_paths: list[str]) -> list[UntrustedContent]:
        paths = WorkspacePaths(self._ws.repo)
        blocks = []
        for rel in rel_paths:
            try:
                path = paths.resolve(rel)
                lines = path.read_text("utf-8", "replace").splitlines()
            except (ToolError, OSError):
                continue
            shown = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(lines[:EXCERPT_LINES], 1))
            if len(lines) > EXCERPT_LINES:
                shown += f"\n[... {len(lines) - EXCERPT_LINES} more lines ...]"
            blocks.append(
                UntrustedContent(
                    shown, "file", (("path", rel), ("lines", f"1-{min(len(lines), EXCERPT_LINES)}"))
                )
            )
        return blocks

    # ------------------------------------------------------------------ 7-9. fix loop
    async def _fix_loop(
        self,
        profile: RepoProfile,
        analysis: IssueAnalysis,
        plan: Plan,
        reproduction: Reproduction,
        baseline: _Baseline,
    ) -> None:
        read_only = frozenset({reproduction.test_file})
        allowed = frozenset(p.path for p in plan.protected_path_changes)
        feedback: str | None = None
        while True:
            await self._edit(
                profile,
                analysis=analysis,
                plan=plan,
                reproduction=reproduction,
                feedback=feedback,
                read_only=read_only,
                allowed_protected=allowed,
            )
            passed, failing_run, comparison = await self._test(profile, reproduction, baseline)
            if passed:
                return
            # A retry is a fix attempt; running out ends the run as budget_exceeded.
            self._budget.before_fix_attempt()
            feedback = await self._debug(plan, reproduction, failing_run, comparison)

    async def _edit(
        self,
        profile: RepoProfile,
        *,
        analysis: IssueAnalysis,
        plan: Plan,
        reproduction: Reproduction,
        feedback: str | None,
        read_only: frozenset[str],
        allowed_protected: frozenset[str],
    ) -> EditReport:
        await self._enter(
            RunStatus.EDITING, "applying the fix" if feedback is None else "retrying the fix"
        )
        task = (
            "Apply this plan.\n\n## Plan\n"
            + compact_json(plan.model_dump(exclude={"rationale"}))
            + "\n\n## Issue analysis\n"
            + compact_json(analysis.model_dump(exclude={"rationale"}))
            + f"\n\n## Reproduction test (read-only; it must pass after your change)\n"
            f"{reproduction.test_file}"
        )
        if feedback:
            task += f"\n\n## Feedback from the previous attempt\n{feedback}"
        result = await self._runtime.tool_loop(
            "editor",
            task,
            [],
            EditReport,
            tools=EDITOR_TOOLS,
            ctx=self._ctx(profile, read_only=read_only, allowed_protected=allowed_protected),
            max_rounds=self._config.editor_rounds,
        )
        diff, stat = await diff_against_base(self._ws.repo, self._base_commit)
        changed = [p for p in changed_paths(diff) if p != reproduction.test_file]
        await self._finish(
            f"Changed {', '.join(changed) or 'nothing'}.",
            {**result.value.model_dump(), "diffstat": stat, "tool_rounds": result.rounds},
            rationale=result.value.rationale,
        )
        return result.value

    async def _test(
        self, profile: RepoProfile, reproduction: Reproduction, baseline: _Baseline
    ) -> tuple[bool, TestRun, TestComparison | None]:
        await self._enter(RunStatus.TESTING, "running the reproduction test and the suite")
        repro = await self._run_tests(profile, "reproduction", [reproduction.test_file])
        suite = await self._run_tests(profile, "suite")
        comparison = None
        if suite.report is not None and baseline.tests.report is not None:
            comparison = compare_to_baseline(baseline.tests.report, suite.report)
        repro_ok = repro.report is not None and reproduction_passes(repro.report)
        suite_ok = comparison is not None and not comparison.regressions
        passed = repro_ok and suite_ok
        if not repro_ok:
            summary = "The reproduction test still fails."
        elif not suite_ok:
            summary = (
                "The reproduction test passes, but the change breaks existing tests: "
                + ", ".join(
                    (comparison.new_failures + comparison.missing)[:5] if comparison else []
                )
            )
        else:
            summary = "The reproduction test passes and the suite has no new failures."
        output: dict[str, Any] = {
            "reproduction_passes": repro_ok,
            "comparison": comparison.model_dump() if comparison else None,
        }
        await self._finish(summary, output, outcome="completed" if passed else "failed")
        return passed, repro if not repro_ok else suite, comparison

    async def _debug(
        self,
        plan: Plan,
        reproduction: Reproduction,
        failing: TestRun,
        comparison: TestComparison | None,
    ) -> str:
        step = await self._enter(RunStatus.DEBUGGING, "working out why the tests fail")
        diff, _stat = await diff_against_base(self._ws.repo, self._base_commit)
        hint = classify_failure(failing)
        regressions = ""
        if comparison is not None and comparison.regressions:
            regressions = (
                "\n\nExisting tests that passed before the change and fail now: "
                + ", ".join(comparison.new_failures + comparison.missing)
            )
        diagnosis = await self._runtime.structured(
            "debugger",
            "The last fix attempt failed. Diagnose it.\n\n## Plan\n"
            + compact_json(plan.model_dump(exclude={"rationale"}))
            + f"\n\n## Reproduction test\n{reproduction.test_file}"
            + f"\n\n## Automatic first guess\n{hint}"
            + regressions,
            [
                UntrustedContent(diff[:20000], "diff"),
                UntrustedContent(failure_digest(failing), "test_output"),
            ],
            DebugDiagnosis,
            step_id=str(step.id),
        )
        await self._recorder.update_run(RunUpdate(fix_attempts=self._budget.fix_attempts))
        await self._finish(
            f"{diagnosis.failure_class}: {diagnosis.root_cause}",
            {**diagnosis.model_dump(), "heuristic": hint, "attempt": self._budget.fix_attempts},
            rationale=diagnosis.rationale,
        )
        if diagnosis.next_action == "give_up":
            category: FailureCategory = (
                "environment" if diagnosis.failure_class == "env_dependency" else "incorrect_fix"
            )
            raise RunFailedError("debugger_gave_up", diagnosis.root_cause, category)
        return (
            f"Failure class: {diagnosis.failure_class}\nRoot cause: {diagnosis.root_cause}\n"
            f"What to change: {diagnosis.guidance}{regressions}"
        )

    # ------------------------------------------------------------------ 10. validate
    async def _validate(
        self,
        profile: RepoProfile,
        analysis: IssueAnalysis,
        plan: Plan,
        reproduction: Reproduction,
        baseline: _Baseline,
    ) -> None:
        step = await self._enter(RunStatus.VALIDATING, "running lint, format and type checks")
        checks = [
            CheckResult(name="reproduction", status="passed", command=[reproduction.test_file]),
            CheckResult(name="test_suite", status="passed", command=profile.test_command),
            command_check(
                "lint",
                profile.lint_command,
                baseline.checks["lint"],
                await self._run_check(profile.lint_command),
            ),
            command_check(
                "format",
                profile.format_check_command,
                baseline.checks["format"],
                await self._run_check(profile.format_check_command),
            ),
            command_check(
                "typecheck",
                profile.typecheck_command,
                baseline.checks["typecheck"],
                await self._run_check(profile.typecheck_command),
            ),
        ]
        validation = ValidationReport(checks=checks)
        diff, stat = await diff_against_base(self._ws.repo, self._base_commit)
        if not diff.strip():
            raise RunFailedError("empty_patch", "the run produced no changes", "invalid_patch")
        flags = review_flags(diff)
        pr = await self._runtime.structured(
            "pr_writer",
            "Write the pull request text for this fix.\n\n## Issue analysis\n"
            + compact_json(analysis.model_dump(exclude={"rationale"}))
            + "\n\n## Plan\n"
            + compact_json(plan.model_dump(exclude={"rationale"}))
            + "\n\n## Validation results\n"
            + compact_json(validation.model_dump())
            + f"\n\n## Reproduction test\n{reproduction.test_file}",
            [UntrustedContent(diff[:30000], "diff")],
            PullRequestText,
            step_id=str(step.id),
        )
        issue_ref = f"#{self._issue.number}" if self._issue.number is not None else None
        body = render_pr_body(
            pr, validation, flags, issue_ref=issue_ref, run_url=self._config.run_url
        )
        digest = _sha256(diff)
        await self._recorder.update_run(
            RunUpdate(
                final_diff=diff,
                final_diff_sha256=digest,
                result={
                    "validation": validation.model_dump(),
                    "review_flags": [f.model_dump() for f in flags],
                    "diffstat": stat,
                    "pull_request": {
                        "title": pr.title,
                        "body": body,
                        "commit_message": pr.commit_message,
                    },
                },
            )
        )
        warnings = ", ".join(f"{c.name} {c.status}" for c in validation.warnings)
        await self._finish(
            "Validation passed" + (f" with warnings: {warnings}." if warnings else "."),
            {
                "validation": validation.model_dump(),
                "review_flags": len(flags),
                "diff_sha256": digest,
            },
            rationale=pr.rationale,
        )
        await self._recorder.transition(
            RunStatus.AWAITING_APPROVAL, "fix validated; waiting for human review"
        )


def _profile_summary(profile: RepoProfile) -> str:
    return compact_json(
        profile.model_dump(
            include={
                "primary_language",
                "framework",
                "package_manager",
                "test_framework",
                "test_command",
                "entry_points",
                "key_directories",
                "test_directories",
            }
        )
    )


def _why_not_failing(run: TestRun) -> str:
    if run.report is None:
        return f"The test did not run: {run.report_error}\n{failure_digest(run)}"
    if run.report.errors:
        return (
            "The test errors instead of failing (a collection, import or fixture problem), "
            f"so it does not demonstrate the bug:\n{failure_digest(run)}"
        )
    if run.report.total == 0:
        return "No tests were collected from the file. Test functions must start with test_."
    return "The tests PASS on the current, buggy code, so they do not demonstrate the bug."


_STATE_CATEGORY: dict[RunStatus, FailureCategory] = {
    RunStatus.LOCALIZING: "localization",
    RunStatus.REPRODUCING: "reproduction",
    RunStatus.EDITING: "incorrect_fix",
    RunStatus.DEBUGGING: "incorrect_fix",
}


def _category_for_state(state: RunStatus | None) -> FailureCategory:
    return _STATE_CATEGORY.get(state, "infra") if state is not None else "infra"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
