"""Render the pull request description (spec 2.8) from the PR writer's structured text.

The model writes the prose fields; everything factual (validation results, test counts,
review flags, the trace link) comes from recorded results, so the description cannot
claim a check that did not run.
"""

from agent.components.schemas import PullRequestText
from agent.validation import CheckResult, ReviewFlag, ValidationReport

_STATUS_LABEL = {
    "passed": "passed",
    "failed": "FAILED",
    "pre_existing": "failing before this change too",
    "not_configured": "not configured in this repository",
    "error": "could not run",
}
_CHECK_LABEL = {
    "reproduction": "Reproduction test fails before the fix and passes after",
    "test_suite": "Existing test suite has no new failures",
    "lint": "Lint",
    "format": "Format check",
    "typecheck": "Type check",
}


def _check_line(check: CheckResult) -> str:
    command = f" (`{' '.join(check.command)}`)" if check.command else ""
    return f"- {_CHECK_LABEL[check.name]}: {_STATUS_LABEL[check.status]}{command}"


def render_pr_body(
    text: PullRequestText,
    validation: ValidationReport,
    flags: list[ReviewFlag],
    *,
    issue_ref: str | None,
    run_url: str,
) -> str:
    sections = []
    summary = text.issue_summary.strip()
    if issue_ref:
        summary = f"{summary}\n\nFixes {issue_ref}."
    sections.append(f"## Issue\n\n{summary}")
    sections.append(f"## Root cause\n\n{text.root_cause.strip()}")
    sections.append("## Changes\n\n" + "\n".join(f"- {c.strip()}" for c in text.changes_made))
    sections.append("## Tests added\n\n" + "\n".join(f"- {t.strip()}" for t in text.tests_added))
    sections.append(
        "## Validation\n\nRun in an isolated sandbox with no network access.\n\n"
        + "\n".join(_check_line(c) for c in validation.checks)
    )
    if flags:
        sections.append(
            "## Needs a closer look\n\n"
            + "\n".join(f"- `{f.path}`: {f.kind.replace('_', ' ')} ({f.detail})" for f in flags)
        )
    sections.append(f"---\nOpened by DevAgent after human approval. [Full run trace]({run_url})")
    return "\n\n".join(sections) + "\n"
