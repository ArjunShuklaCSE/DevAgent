"""Deterministic judgement of test results, checks and review flags; PR body rendering."""

import pytest

from agent.components.schemas import PullRequestText
from agent.pr_text import render_pr_body
from agent.validation import (
    CheckResult,
    ValidationReport,
    classify_failure,
    command_check,
    compare_to_baseline,
    reproduction_fails,
    reproduction_passes,
    review_flags,
)
from sandbox.docker_sandbox import CommandResult, TestRun
from sandbox.junit import Outcome, TestCaseResult, TestReport


def report(**cases: Outcome) -> TestReport:
    items = [TestCaseResult(test_id=t, outcome=o, duration_seconds=0) for t, o in cases.items()]
    count = {
        o: sum(1 for c in items if c.outcome == o) for o in ("passed", "failed", "error", "skipped")
    }
    return TestReport(
        total=len(items),
        passed=count["passed"],
        failed=count["failed"],
        errors=count["error"],
        skipped=count["skipped"],
        duration_seconds=0,
        cases=items,
    )


def command(code: int | None, out: str = "", *, timed_out: bool = False) -> CommandResult:
    return CommandResult(
        argv=("x",),
        profile="run",
        exit_code=code,
        stdout=out,
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_seconds=0,
        timed_out=timed_out,
    )


def test_only_tests_that_passed_at_baseline_count_as_regressions() -> None:
    baseline = report(a="passed", b="failed", c="passed", d="skipped")
    current = report(a="failed", b="failed", new="failed", d="passed")
    comparison = compare_to_baseline(baseline, current)
    assert comparison.new_failures == ["a"]
    assert comparison.missing == ["c"]  # a test that stopped running is a regression too
    assert comparison.regressions


def test_no_regressions() -> None:
    comparison = compare_to_baseline(report(a="passed", b="failed"), report(a="passed", b="failed"))
    assert not comparison.regressions


def test_a_reproduction_must_fail_not_error() -> None:
    assert reproduction_fails(report(t="failed"))
    assert not reproduction_fails(report(t="passed"))
    assert not reproduction_fails(report(t="failed", u="error"))  # a broken test
    assert not reproduction_fails(report())
    assert reproduction_passes(report(t="passed"))
    assert not reproduction_passes(report())


@pytest.mark.parametrize(
    ("message", "timed_out", "expected"),
    [
        ("E   AssertionError: assert None == 0.0", False, "assertion"),
        ("E     File 'x.py', line 3\nE   SyntaxError: invalid syntax", False, "syntax"),
        ("ImportError: cannot import name 'mean' from 'calc'", False, "import_error"),
        ("ModuleNotFoundError: No module named 'yaml'", False, "env_dependency"),
        ("", True, "timeout"),
    ],
)
def test_failure_hint(message: str, timed_out: bool, expected: str) -> None:
    failing = TestCaseResult(test_id="t", outcome="failed", duration_seconds=0, message=message)
    run = TestRun(
        result=command(1, timed_out=timed_out),
        report=TestReport(
            total=1, passed=0, failed=1, errors=0, skipped=0, duration_seconds=0, cases=[failing]
        ),
    )
    assert classify_failure(run) == expected


def test_checks_are_judged_against_the_baseline() -> None:
    cmd = ["ruff", "check", "."]
    assert command_check("lint", None, None, None).status == "not_configured"
    assert command_check("lint", cmd, command(0), command(0)).status == "passed"
    assert command_check("lint", cmd, command(0), command(1, "E501")).status == "failed"
    assert command_check("lint", cmd, command(1), command(1)).status == "pre_existing"
    assert command_check("lint", cmd, command(0), command(None, timed_out=True)).status == "error"


def test_lint_problems_are_warnings_not_blockers() -> None:
    validation = ValidationReport(
        checks=[
            CheckResult(name="reproduction", status="passed"),
            CheckResult(name="test_suite", status="passed"),
            CheckResult(name="lint", status="failed"),
        ]
    )
    assert validation.passed
    assert [c.name for c in validation.warnings] == ["lint"]


DIFF = """\
diff --git a/requirements.txt b/requirements.txt
--- a/requirements.txt
+++ b/requirements.txt
@@ -1 +1,2 @@
 pytest
+telemetry-client==1.0
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1 @@
-on: push
+on: [push, pull_request]
diff --git a/calc/stats.py b/calc/stats.py
--- a/calc/stats.py
+++ b/calc/stats.py
@@ -1,2 +1,4 @@
+import urllib.request
+urllib.request.urlopen("https://collector.example/x")
 def mean(values):
diff --git a/tests/test_old.py b/tests/test_old.py
deleted file mode 100644
--- a/tests/test_old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def test_x():
-    assert True
diff --git a/tests/test_stats.py b/tests/test_stats.py
--- a/tests/test_stats.py
+++ b/tests/test_stats.py
@@ -1,6 +1,3 @@
-def test_empty():
-    assert mean([]) == 0.0
 def test_mean():
-    assert mean([1]) == 1
+    assert mean([1]) == 1.0
"""


def test_review_flags_cover_the_spec_cases() -> None:
    flags = {(f.kind, f.path) for f in review_flags(DIFF)}
    assert ("sensitive_path", "requirements.txt") in flags
    assert ("new_dependency", "requirements.txt") in flags
    assert ("protected_path", ".github/workflows/ci.yml") in flags
    assert ("network_call", "calc/stats.py") in flags
    assert ("deleted_test", "tests/test_old.py") in flags
    assert ("deleted_test", "tests/test_stats.py") in flags  # test_empty removed
    removed = [f.detail for f in review_flags(DIFF) if f.path == "tests/test_stats.py"]
    assert removed == ["removed test_empty"]  # test_mean was edited, not removed


def test_a_plain_fix_has_no_flags() -> None:
    assert review_flags("") == []
    plain = "diff --git a/calc/stats.py b/calc/stats.py\n+++ b/calc/stats.py\n+    return 0.0\n"
    assert review_flags(plain) == []


def test_pr_body_uses_recorded_results() -> None:
    text = PullRequestText(
        rationale="r",
        title="Return 0.0 from mean() for an empty list",
        issue_summary="mean([]) crashed.",
        root_cause="Division by len(values).",
        changes_made=["guard the empty case"],
        tests_added=["tests/test_mean_empty.py"],
        commit_message="Fix mean for empty input",
    )
    validation = ValidationReport(
        checks=[
            CheckResult(name="reproduction", status="passed", command=["tests/test_mean_empty.py"]),
            CheckResult(name="test_suite", status="passed", command=["python", "-m", "pytest"]),
            CheckResult(name="lint", status="pre_existing", command=["ruff", "check", "."]),
            CheckResult(name="typecheck", status="not_configured"),
        ]
    )
    body = render_pr_body(
        text, validation, review_flags(DIFF)[:1], issue_ref="#7", run_url="https://x/runs/1"
    )
    assert body.startswith("## Issue\n\nmean([]) crashed.\n\nFixes #7.")
    assert "- Lint: failing before this change too (`ruff check .`)" in body
    assert "- Type check: not configured in this repository" in body
    assert "## Needs a closer look\n\n- `requirements.txt`: sensitive path" in body
    assert body.rstrip().endswith("[Full run trace](https://x/runs/1)")
    no_ref = render_pr_body(text, validation, [], issue_ref=None, run_url="u")
    assert "Fixes" not in no_ref
    assert "Needs a closer look" not in no_ref
