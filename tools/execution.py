"""Sandbox tools: run_command and run_tests. Nothing here runs on the worker."""

from typing import ClassVar

from pydantic import Field

from core.tools import ToolCapability
from sandbox.docker_sandbox import CommandResult
from sandbox.junit import TestReport
from tools.base import BaseTool, SandboxRunner, ToolContext, ToolInput, ToolOutput
from tools.errors import ToolError

MAX_FAILURES_SHOWN = 10
MAX_FAILURE_MESSAGE_CHARS = 1500


def _sandbox(ctx: ToolContext) -> SandboxRunner:
    if ctx.sandbox is None:
        raise ToolError("sandbox_unavailable", "no sandbox is attached to this run")
    return ctx.sandbox


def command_timeout(requested: float | None, ctx: ToolContext) -> float | None:
    """The requested timeout, capped by the run's command timeout."""
    if ctx.command_timeout_seconds is None:
        return requested
    if requested is None:
        return ctx.command_timeout_seconds
    return min(requested, ctx.command_timeout_seconds)


def render_command(result: CommandResult) -> str:
    status = "timed out" if result.timed_out else f"exit code {result.exit_code}"
    if result.oom_killed:
        status += " (killed: out of memory)"
    parts = [f"$ {' '.join(result.argv)}", f"[{status}, {result.duration_seconds:.1f}s]"]
    if result.stdout:
        parts += ["--- stdout ---", result.stdout.rstrip()]
    if result.stderr:
        parts += ["--- stderr ---", result.stderr.rstrip()]
    return "\n".join(parts)


def command_data(result: CommandResult) -> dict[str, object]:
    return {
        "argv": list(result.argv),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "oom_killed": result.oom_killed,
        "duration_seconds": result.duration_seconds,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
    }


class RunCommandInput(ToolInput):
    argv: list[str] = Field(
        min_length=1,
        max_length=64,
        description="command as an argument list, e.g. ['python', 'repro.py']; no shell syntax",
    )
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class RunCommandTool(BaseTool[RunCommandInput]):
    name: ClassVar[str] = "run_command"
    description: ClassVar[str] = (
        "Run a command in the sandbox (no network, repository mounted at /workspace). "
        "argv only: no shell, pipes or redirects. Allowed: python <file.py>, python -m "
        "pytest/mypy/ruff/..., pytest, ruff, mypy, ls, cat, head, tail, wc, grep, find, diff."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.EXECUTE_SANDBOX
    input_model = RunCommandInput
    max_output_chars: ClassVar[int] = 20_000

    async def run(self, args: RunCommandInput, ctx: ToolContext) -> ToolOutput:
        result = await _sandbox(ctx).run(
            ctx.workspace, args.argv, timeout_seconds=command_timeout(args.timeout_seconds, ctx)
        )
        return ToolOutput(text=render_command(result), data=command_data(result))


class RunTestsInput(ToolInput):
    selector: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="optional test files or node ids, e.g. ['tests/test_x.py::test_y']",
    )
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class RunTestsTool(BaseTool[RunTestsInput]):
    name: ClassVar[str] = "run_tests"
    description: ClassVar[str] = (
        "Run the repository's test command in the sandbox, optionally limited to some "
        "tests. Returns pass/fail counts and the failing tests with their messages."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.EXECUTE_SANDBOX
    input_model = RunTestsInput
    max_output_chars: ClassVar[int] = 20_000

    async def run(self, args: RunTestsInput, ctx: ToolContext) -> ToolOutput:
        if not ctx.test_command:
            raise ToolError("no_test_command", "no test command was detected for this repository")
        for item in args.selector:
            if item.startswith("-"):
                raise ToolError("argument_not_allowed", f"selector {item!r} looks like an option")
        run = await _sandbox(ctx).run_tests(
            ctx.workspace,
            ctx.test_command,
            args.selector,
            timeout_seconds=command_timeout(args.timeout_seconds, ctx),
        )
        data: dict[str, object] = {"command": command_data(run.result)}
        if run.report is None:
            data["report_error"] = run.report_error
            text = f"[no structured report: {run.report_error}]\n{render_command(run.result)}"
            return ToolOutput(text=text, data=data)
        data["report"] = run.report.model_dump(mode="json")
        return ToolOutput(text=render_report(run.report, run.result), data=data)


def render_report(report: TestReport, result: CommandResult) -> str:
    summary = (
        f"{report.total} tests: {report.passed} passed, {report.failed} failed, "
        f"{report.errors} errors, {report.skipped} skipped ({result.duration_seconds:.1f}s)"
    )
    if result.timed_out:
        summary += " [timed out]"
    lines = [summary]
    failing = report.failing()
    for case in failing[:MAX_FAILURES_SHOWN]:
        lines.append(f"\n{case.outcome.upper()}: {case.test_id}")
        if case.message:
            lines.append(case.message[:MAX_FAILURE_MESSAGE_CHARS])
    if len(failing) > MAX_FAILURES_SHOWN:
        lines.append(f"\n[... {len(failing) - MAX_FAILURES_SHOWN} more failing tests ...]")
    if report.total == 0 and result.stdout:
        lines += ["--- output ---", result.stdout[-4000:]]
    return "\n".join(lines)
