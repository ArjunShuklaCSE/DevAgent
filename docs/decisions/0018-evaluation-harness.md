# 0018. Evaluation: hidden tests in a fresh sandbox, in-process harness, Wilson intervals

- Status: Accepted (Phase 9)
- Date: 2026-09-27

## Context
Spec section 13 asks for a benchmark harness:
- A dataset format with hidden fail-to-pass and pass-to-pass tests.
- Resolved-rate and related metrics, a failure taxonomy, cost and time.
- A report with the model, prompt versions and configuration.
- An ablation, and a dashboard page that shows only stored numbers.

Two things must hold:
- The agent can never see the hidden tests or the reference fix.
- A run can never pass by leaving state behind in its own workspace.

## Decision
- **Dataset format.**
  - A YAML file (`evaluation/datasets/<name>.yaml`) plus a directory of the same name
    that holds hidden test files and gold patches.
  - Each case names a bundled sample repository (`sample:<name>`), the issue text,
    the hidden test files (repo path → dataset file) and the F2P and P2P tests as
    pytest node ids.
  - The loader rejects unknown keys, bad node ids, F2P tests outside the hidden files,
    paths that escape the dataset directory and missing files.
  - The file's SHA-256 is stored. A changed dataset must bump its version, so stored
    results stay comparable.
- **Only sample repositories in v1.** Agent runs check out a repository's default
  branch; pinning a GitHub commit would need a ref on the run, which the run model
  does not have yet.
- **The agent never sees the dataset.** Hidden tests and gold patches live under
  `evaluation/datasets/`, outside `sample_repos/`, which is all a run can copy.
- **Scoring in a fresh sandbox.**
  - The final diff (the one a reviewer would approve) is applied with `git apply` to
    a new checkout of the base.
  - The hidden files are written on top, dependencies are installed from scratch in a
    new sandbox, and only the F2P and P2P node ids run.
  - Outcomes are read from JUnit XML. A test missing from the report counts as not
    passing, so deleting a test cannot hide a regression.
  - Resolved means: the patch applied, every F2P test passed, no P2P test regressed,
    and for adversarial cases the adversarial check passed.
- **Adversarial check.** For cases marked adversarial, the run fails if the diff:
  - adds a dependency,
  - adds a network or subprocess call,
  - deletes a test or touches CI (the Phase 6 review flags),
  - or the diff or PR text dumps the environment or a secret-looking assignment.
- **The harness runs in-process, in the worker container** (`devagent eval run`).
  - Each case is a normal agent run: same `run_agent` composition root, sandbox,
    database records and dashboard trace.
  - The run's config records the evaluation run, case and repeat.
  - No queue: the harness needs the result before scoring, and holding a worker slot
    per case would starve dashboard runs.
- **Evaluation runs are never approved or published.** They stay in
  `awaiting_approval`. Approval stays a human decision, and scoring does not need it.
- **Failure taxonomy.**
  - A stopped run uses its recorded category: budget → `budget_exceeded`, wall clock
    → `timeout`.
  - Otherwise the score decides: invalid patch, environment, incorrect fix (F2P
    failed, or adversarial check failed), regression (P2P broke).
- **Statistics.** Rates carry a 95% Wilson score interval, which stays inside [0, 1]
  and is sensible at 0/n and n/n. Medians are used for steps, time and tokens because
  they are skewed.
- **Scripted model.** Cases without a recorded cassette are listed as "not run", not
  scored as failures. The report and page say that a scripted run shows the harness
  working and nothing about model quality.
- **Ablation.** `--max-fix-attempts 0` disables the debug loop. `devagent eval report
  <run> --compare <run>` renders both side by side.
- **Dashboard.** It reads `GET /api/v1/evaluation/runs[/{id}]`, which is built only
  from `eval_results` rows. An import-linter contract keeps the read models
  (`evaluation.metrics`, `stats`, `report`) independent of the web layer and the
  agent.

## Consequences
- Scoring reinstalls dependencies per case, which costs about 10 s per case on the
  starter set. That is the price of a clean environment.
- Real resolve rates need a real model and its key. Until then, only the slugger case
  (the one with a recorded cassette) runs, and the stored numbers say exactly that.
- Supporting GitHub-hosted cases needs a base-commit ref on agent runs and a
  commit-pinned clone for scoring.
