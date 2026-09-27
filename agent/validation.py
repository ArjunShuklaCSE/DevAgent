"""Deterministic checks around the model's work: test comparison, failure hints,
validation results and review flags for the approval screen (spec 4.4 Validator, 7.5).

Nothing here calls a model. The orchestrator runs the commands; these functions decide
what the results mean, so the rules are unit-tested and the same for every run.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sandbox.docker_sandbox import CommandResult, TestRun
from sandbox.junit import TestReport
from tools.sensitive import classify_path

CheckStatus = Literal["passed", "failed", "pre_existing", "not_configured", "error"]
FailureHint = Literal["assertion", "import_error", "syntax", "env_dependency", "timeout", "flaky"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- tests


class TestComparison(_Model):
    """The current suite compared with the baseline taken before any change."""

    __test__ = False

    total: int
    failing: list[str]
    new_failures: list[str] = Field(description="passed at baseline, fail now")
    missing: list[str] = Field(description="ran at baseline, did not run now")

    @property
    def regressions(self) -> bool:
        return bool(self.new_failures or self.missing)


def compare_to_baseline(baseline: TestReport, current: TestReport) -> TestComparison:
    before = {c.test_id: c.outcome for c in baseline.cases}
    now = {c.test_id: c.outcome for c in current.cases}
    failing = sorted(t for t, o in now.items() if o in ("failed", "error"))
    new_failures = sorted(t for t in failing if before.get(t) == "passed")
    missing = sorted(t for t, o in before.items() if o != "skipped" and t not in now)
    return TestComparison(
        total=len(now), failing=failing, new_failures=new_failures, missing=missing
    )


def reproduction_fails(report: TestReport) -> bool:
    """The reproduction test demonstrates the bug: at least one test fails on an
    assertion or exception in the test body, and none fail to even run (errors are
    collection or fixture problems, i.e. a broken test, not the bug)."""
    return report.failed > 0 and report.errors == 0


def reproduction_passes(report: TestReport) -> bool:
    return report.total > 0 and report.failed == 0 and report.errors == 0


def classify_failure(run: TestRun) -> FailureHint:
    """A first guess at why tests failed, given to the debugger as a hint."""
    if run.result.timed_out:
        return "timeout"
    text = _failure_text(run)
    if re.search(r"\b(SyntaxError|IndentationError|TabError)\b", text):
        return "syntax"
    if re.search(r"\bNo module named\b|\bModuleNotFoundError\b", text) and _third_party(text):
        return "env_dependency"
    if re.search(r"\b(ImportError|ModuleNotFoundError)\b|cannot import name", text):
        return "import_error"
    return "assertion"


def _failure_text(run: TestRun) -> str:
    parts = [run.result.stdout[-4000:], run.result.stderr[-4000:]]
    if run.report is not None:
        parts += [c.message or "" for c in run.report.failing()]
    return "\n".join(parts)


def _third_party(text: str) -> bool:
    # "No module named 'yaml'" for a module the repository does not contain.
    return bool(re.search(r"No module named '([A-Za-z_][\w]*)'", text))


def failure_digest(run: TestRun, limit: int = 6000) -> str:
    """Failing tests and their messages, for prompts (the caller wraps it as untrusted)."""
    lines: list[str] = []
    if run.result.timed_out:
        lines.append("[the test command timed out]")
    if run.report is None:
        lines.append(f"[no test report: {run.report_error}]")
        lines.append(run.result.stdout[-3000:])
        lines.append(run.result.stderr[-3000:])
    else:
        r = run.report
        lines.append(
            f"{r.total} tests: {r.passed} passed, {r.failed} failed, {r.errors} errors, "
            f"{r.skipped} skipped"
        )
        for case in r.failing()[:10]:
            lines.append(f"\n{case.outcome.upper()}: {case.test_id}")
            if case.message:
                lines.append(case.message[:1500])
    return "\n".join(lines)[:limit]


# --------------------------------------------------------------------------- validation


class CheckResult(_Model):
    name: Literal["reproduction", "test_suite", "lint", "format", "typecheck"]
    status: CheckStatus
    command: list[str] | None = None
    detail: str = ""


class ValidationReport(_Model):
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        """Blocking checks passed; lint, format and type problems are warnings."""
        blocking = {"reproduction", "test_suite"}
        return all(c.status == "passed" for c in self.checks if c.name in blocking)

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status in ("failed", "error")]


def command_check(
    name: Literal["lint", "format", "typecheck"],
    command: list[str] | None,
    baseline: CommandResult | None,
    current: CommandResult | None,
) -> CheckResult:
    """A lint, format or type check, judged against the same command at baseline.

    ``pre_existing`` means the check already failed before the change, so the change
    is not blamed for it.
    """
    if command is None or current is None:
        return CheckResult(name=name, status="not_configured")
    if current.timed_out:
        return CheckResult(name=name, status="error", command=command, detail="timed out")
    if current.ok:
        return CheckResult(name=name, status="passed", command=command)
    detail = (current.stdout or current.stderr)[-2000:]
    if baseline is not None and not baseline.ok:
        return CheckResult(name=name, status="pre_existing", command=command, detail=detail)
    return CheckResult(name=name, status="failed", command=command, detail=detail)


# --------------------------------------------------------------------------- review flags


class ReviewFlag(_Model):
    kind: Literal[
        "sensitive_path", "protected_path", "new_dependency", "network_call", "deleted_test"
    ]
    path: str
    detail: str


_DEPENDENCY_FILES = re.compile(
    r"(^|/)(requirements[^/]*\.(txt|in)|pyproject\.toml|setup\.py|setup\.cfg|Pipfile"
    r"|package\.json)$"
)
_NETWORK = re.compile(
    r"\b(requests\.|httpx\.|aiohttp\.|urllib\.request|urlopen\(|http\.client|socket\."
    r"|smtplib|ftplib|subprocess\.|os\.system\(|os\.popen\()"
)
_TEST_DEF = re.compile(r"^-\s*(async\s+)?def\s+(test_\w+)")


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}"


def review_flags(diff: str) -> list[ReviewFlag]:
    """Changes a reviewer must look at (spec 7.5), from a ``git diff`` of the run."""
    flags: list[ReviewFlag] = []
    for path, deleted, body in _split_diff(diff):
        path_class = classify_path(path)
        if path_class == "protected":
            flags.append(ReviewFlag(kind="protected_path", path=path, detail="CI or lockfile"))
        elif path_class == "sensitive":
            flags.append(
                ReviewFlag(kind="sensitive_path", path=path, detail="build or test configuration")
            )
        added = [line[1:] for line in body if line.startswith("+") and not line.startswith("+++")]
        if _DEPENDENCY_FILES.search(path) and added:
            flags.append(
                ReviewFlag(
                    kind="new_dependency",
                    path=path,
                    detail="; ".join(a.strip() for a in added if a.strip())[:300],
                )
            )
        for line in added:
            match = _NETWORK.search(line)
            if match and path.endswith(".py"):
                flags.append(ReviewFlag(kind="network_call", path=path, detail=line.strip()[:200]))
        if deleted and _is_test_path(path):
            flags.append(ReviewFlag(kind="deleted_test", path=path, detail="test file deleted"))
        elif _is_test_path(path):
            removed_tests = [m.group(2) for line in body if (m := _TEST_DEF.match(line))]
            kept = {
                m.group(2)
                for line in body
                if (m := re.match(r"^\+\s*(async\s+)?def\s+(test_\w+)", line))
            }
            for name in removed_tests:
                if name not in kept:
                    flags.append(
                        ReviewFlag(kind="deleted_test", path=path, detail=f"removed {name}")
                    )
    return flags


def _split_diff(diff: str) -> list[tuple[str, bool, list[str]]]:
    """(path, deleted, lines) per file in a unified git diff."""
    files: list[tuple[str, bool, list[str]]] = []
    current: list[str] | None = None
    path = ""
    deleted = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                files.append((path, deleted, current))
            current = []
            path = line.rsplit(" b/", 1)[-1]
            deleted = False
            continue
        if current is None:
            continue
        if line.startswith("deleted file mode"):
            deleted = True
        current.append(line)
    if current is not None:
        files.append((path, deleted, current))
    return files


def changed_paths(diff: str) -> list[str]:
    return [path for path, _deleted, _lines in _split_diff(diff)]
