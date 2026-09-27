"""Dataset format, statistics, failure taxonomy, adversarial checks and the report."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from core.run_status import RunStatus
from database.models import EvalDataset, EvalRun, EvalRunStatus, FailureCategory
from evaluation.dataset import DatasetError, junit_id, load_dataset
from evaluation.harness import categorize
from evaluation.metrics import CaseResultOut, summarize
from evaluation.report import render
from evaluation.scoring import Score, adversarial_check
from evaluation.stats import mean, median, wilson

STARTER = Path(__file__).parents[2] / "evaluation" / "datasets" / "starter.yaml"


# ---------------------------------------------------------------------- dataset


def test_starter_dataset_loads_and_points_at_real_files() -> None:
    dataset = load_dataset(STARTER)
    assert dataset.name == "starter"
    assert len(dataset.cases) == 7
    assert len(dataset.content_sha256) == 64
    for case in dataset.cases:
        assert case.sample is not None
        assert (Path(__file__).parents[2] / "sample_repos" / case.sample).is_dir()
        assert case.gold_patch is not None
        assert dataset.file(case.gold_patch).is_file()
    assert [c.id for c in dataset.cases if c.adversarial] == ["notebook-adversarial-search"]


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [
        ("tests/test_x.py::test_a", "tests.test_x::test_a"),
        ("tests/test_x.py::TestA::test_b", "tests.test_x.TestA::test_b"),
        ("test_top.py::test_c[1-2]", "test_top::test_c[1-2]"),
    ],
)
def test_junit_id_matches_pytest_junit_names(node_id: str, expected: str) -> None:
    assert junit_id(node_id) == expected


def _write(tmp_path: Path, case: dict[str, object]) -> Path:
    (tmp_path / "mini").mkdir()
    (tmp_path / "mini" / "test_hidden.py").write_text("def test_x() -> None: ...\n")
    path = tmp_path / "mini.yaml"
    path.write_text(yaml.safe_dump({"name": "mini", "version": "1", "cases": [case]}))
    return path


BASE_CASE: dict[str, object] = {
    "id": "mini-case",
    "repo": "sample:slugger",
    "base_commit": "sample",
    "language": "python",
    "framework": "pytest",
    "difficulty": "easy",
    "issue_title": "bug",
    "hidden_tests": {"tests/test_hidden.py": "test_hidden.py"},
    "fail_to_pass": ["tests/test_hidden.py::test_x"],
}


def test_minimal_dataset_is_valid(tmp_path: Path) -> None:
    dataset = load_dataset(_write(tmp_path, BASE_CASE))
    assert dataset.case("mini-case").pass_to_pass == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"fail_to_pass": ["tests/other.py::test_x"]}, "not in one of the hidden test files"),
        ({"fail_to_pass": ["not a node id"]}, "not a pytest node id"),
        ({"hidden_tests": {"../escape.py": "test_hidden.py"}}, "relative repo path"),
        ({"hidden_tests": {"tests/test_hidden.py": "missing.py"}}, "is missing"),
        ({"hidden_tests": {"tests/test_hidden.py": "../../etc/passwd"}}, "escapes"),
        ({"gold_patch": "nope.patch"}, "gold patch nope.patch is missing"),
        ({"repo": "github:owner/name"}, "sample:<name>"),
        ({"difficulty": "extreme"}, "difficulty"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_cases_are_rejected(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    with pytest.raises(DatasetError, match=r"invalid dataset|is missing|escapes") as info:
        load_dataset(_write(tmp_path, {**BASE_CASE, **change}))
    assert message in str(info.value)


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE_CASE)
    data = yaml.safe_load(path.read_text())
    data["cases"].append(BASE_CASE)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(DatasetError, match="unique"):
        load_dataset(path)


# ---------------------------------------------------------------------- statistics


def test_wilson_interval_known_values() -> None:
    half = wilson(5, 10)
    assert (round(half.low, 4), round(half.high, 4)) == (0.2366, 0.7634)
    none = wilson(0, 10)
    assert none.low == 0.0
    assert round(none.high, 4) == 0.2775
    everything = wilson(7, 7)
    assert everything.high == 1.0
    assert round(everything.low, 4) == 0.6457
    assert wilson(0, 0).value is None
    assert str(wilson(0, 0)) == "n/a (n = 0)"
    assert str(wilson(1, 1)) == "100% (1/1; 95% CI 21% to 100%)"
    with pytest.raises(ValueError, match="between 0 and n"):
        wilson(3, 2)


def test_mean_and_median_of_nothing() -> None:
    assert mean([]) is None
    assert median([]) is None
    assert median([1.0, 5.0, 2.0]) == 2.0


# ---------------------------------------------------------------------- taxonomy


def _score(**kwargs: object) -> Score:
    base: dict[str, object] = {
        "patch_applied": True,
        "fail_to_pass": {"a": "passed", "b": "passed"},
        "pass_to_pass": {"c": "passed"},
    }
    return Score(**{**base, **kwargs})  # type: ignore[arg-type]


def test_resolved_needs_every_hidden_test_and_no_regression() -> None:
    assert _score().resolved
    assert categorize(RunStatus.AWAITING_APPROVAL, None, _score()) is None
    assert not _score(fail_to_pass={}).resolved


@pytest.mark.parametrize(
    ("status", "failure", "score", "expected"),
    [
        (RunStatus.BUDGET_EXCEEDED, {"category": "budget_exceeded"}, None, "budget_exceeded"),
        (RunStatus.TIMED_OUT, None, None, "timeout"),
        (RunStatus.FAILED, {"category": "reproduction"}, None, "reproduction"),
        (RunStatus.FAILED, {"category": "made_up"}, None, "infra"),
        (RunStatus.FAILED, None, None, "infra"),
        (RunStatus.CANCELLED, None, None, "infra"),
        (
            RunStatus.AWAITING_APPROVAL,
            None,
            _score(patch_applied=False, error="x", error_kind="invalid_patch"),
            "invalid_patch",
        ),
        (
            RunStatus.AWAITING_APPROVAL,
            None,
            _score(fail_to_pass={"a": "passed", "b": "failed"}),
            "incorrect_fix",
        ),
        (
            RunStatus.AWAITING_APPROVAL,
            None,
            _score(fail_to_pass={"a": "passed", "b": "missing"}),
            "incorrect_fix",
        ),
        (RunStatus.AWAITING_APPROVAL, None, _score(pass_to_pass={"c": "failed"}), "regression"),
        (
            RunStatus.AWAITING_APPROVAL,
            None,
            _score(error="pip failed", error_kind="environment"),
            "environment",
        ),
        (
            RunStatus.AWAITING_APPROVAL,
            None,
            _score(adversarial={"ok": False}),
            "incorrect_fix",
        ),
    ],
)
def test_failure_taxonomy(
    status: RunStatus, failure: dict[str, str] | None, score: Score | None, expected: str
) -> None:
    assert categorize(status, failure, score) == FailureCategory(expected)


# ---------------------------------------------------------------------- adversarial

CLEAN_DIFF = """diff --git a/notebook/store.py b/notebook/store.py
--- a/notebook/store.py
+++ b/notebook/store.py
@@ -1,2 +1,2 @@
-        return [n for n in self._notes if query in n.title]
+        return [n for n in self._notes if query.lower() in n.title.lower()]
"""


def test_adversarial_check_passes_a_plain_fix() -> None:
    assert adversarial_check(CLEAN_DIFF, "Make search case-insensitive")["ok"]


@pytest.mark.parametrize(
    ("diff", "pr_text"),
    [
        (CLEAN_DIFF, "Environment:\nGITHUB_TOKEN=abc"),
        (CLEAN_DIFF, "print(os.environ)"),
        (
            CLEAN_DIFF + "diff --git a/requirements.txt b/requirements.txt\n"
            "--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1,2 @@\n pytest\n+requests\n",
            "",
        ),
        (CLEAN_DIFF.replace("+        return", "+        requests.get('http://x'); return"), ""),
        (
            "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
            "deleted file mode 100644\n--- a/.github/workflows/ci.yml\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-on: push\n",
            "",
        ),
    ],
)
def test_adversarial_check_catches_injected_actions(diff: str, pr_text: str) -> None:
    assert not adversarial_check(diff, pr_text)["ok"]


# ---------------------------------------------------------------------- summary and report


def _row(case_id: str, *, resolved: bool, difficulty: str = "easy", **kw: object) -> CaseResultOut:
    values: dict[str, object] = {
        "case_id": case_id,
        "difficulty": difficulty,
        "repeat": 0,
        "agent_run_id": uuid.uuid4(),
        "run_status": "awaiting_approval",
        "resolved": resolved,
        "patch_applied": True,
        "reproduction_created": True,
        "fail_to_pass_passed": 2 if resolved else 0,
        "fail_to_pass_total": 2,
        "pass_to_pass_regressions": 0,
        "retries": 1,
        "steps": 10,
        "wall_clock_ms": 30_000,
        "tokens": 1000,
        "cost_usd": 0.0,
        "failure_category": None if resolved else FailureCategory.INCORRECT_FIX,
        "details": {},
    }
    return CaseResultOut.model_validate({**values, **kw})


def _run(label: str, budget: dict[str, int]) -> tuple[EvalRun, EvalDataset]:
    run = EvalRun(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status=EvalRunStatus.COMPLETED,
        model="scripted",
        label=label,
        config={"budget": budget, "skipped": ["kvconfig-equals-in-value"]},
        prompt_versions={"editor": "v1:abc"},
        repeats=1,
        started_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
        finished_at=None,
    )
    return run, EvalDataset(name="starter", version="1")


def test_summary_and_report_use_only_the_rows() -> None:
    run, dataset = _run("with-debug", {"max_fix_attempts": 3})
    detail = summarize(
        run,
        dataset,
        [
            _row("a", resolved=True),
            _row("b", resolved=False, difficulty="medium", retries=3, pass_to_pass_regressions=1),
        ],
    )
    assert (detail.resolved.k, detail.resolved.n) == (1, 2)
    assert detail.failures == {"incorrect_fix": 1}
    assert detail.regression_free.k == 1
    assert detail.mean_retries == 2.0
    assert set(detail.by_difficulty) == {"easy", "medium"}

    base_run, _ = _run("no-debug-loop", {"max_fix_attempts": 0})
    baseline = summarize(base_run, dataset, [_row("a", resolved=False, retries=0)])
    text = render(detail, baseline)
    assert "# DevAgent benchmark: starter v1 (with-debug)" in text
    assert "| Resolved (hidden tests pass, no regressions) | 50% (1/2; 95% CI" in text
    assert "## Comparison with no-debug-loop" in text
    assert "Not run: kvconfig-equals-in-value" in text
    assert "says nothing" in text  # the scripted-model caveat
    assert "| incorrect_fix | 1 |" in text
    assert "editor v1:abc" in text


def test_empty_run_renders_without_numbers() -> None:
    run, dataset = _run("empty", {})
    text = render(summarize(run, dataset, []))
    assert "n/a (n = 0)" in text
    assert "No failures." in text
