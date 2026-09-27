# Evaluation

DevAgent's benchmark harness runs the agent on issues with known fixes, then scores its
diff with tests the agent never saw. Design notes: [ADR 0018](decisions/0018-evaluation-harness.md).

## Dataset format

A dataset is a YAML file plus a directory of the same name next to it:

```
evaluation/datasets/
  starter.yaml
  starter/
    slugger/test_hidden_slug.py   # hidden tests
    slugger/gold.patch            # reference fix (for validating the case)
```

Each case:

```yaml
- id: slugger-empty-title
  repo: sample:slugger              # a repository under sample_repos/
  base_commit: sample
  language: python
  framework: pytest
  difficulty: easy                  # easy | medium | hard
  issue_title: slugify crashes on titles without letters
  issue_number: 3
  issue_body: |
    ...
  hidden_tests:                     # repo path -> file in the dataset directory
    tests/test_hidden_slug.py: slugger/test_hidden_slug.py
  fail_to_pass:                     # must fail before and pass after the fix
    - tests/test_hidden_slug.py::test_empty_title_gives_empty_slug
  pass_to_pass:                     # must keep passing
    - tests/test_slug.py::test_basic
  gold_patch: slugger/gold.patch
  cassette: tests/cassettes/slugger_fix.yaml   # optional: replay for the scripted model
  adversarial: false                # true: also fail on injected actions
```

`devagent eval validate` checks the format. `devagent eval validate --gold` also runs
every case in the sandbox and checks two things: on the base, the F2P tests fail and
the P2P tests pass; with the gold patch, the case counts as resolved.

## The starter dataset

Seven small Python and pytest repositories, one bug each:

| Case | Bug | Difficulty |
| --- | --- | --- |
| slugger-empty-title | `IndexError` on titles without letters | easy |
| kvconfig-equals-in-value | values containing `=` are cut off | easy |
| textchunk-last-chunk | the final partial chunk is dropped | easy |
| tagstore-mutable-default | items created without tags share one tag list | medium |
| tzconvert-offset | `parse_iso` ignores the UTC offset | medium |
| wallet-exception-type | overdraft raises `ValueError` instead of `InsufficientFundsError` | easy |
| notebook-adversarial-search | case-sensitive search, with prompt injections in the README, code and issue | medium |

## Adding a case

1. Put the buggy project under `sample_repos/<name>/`: a Python package, its tests, and
   a `pyproject.toml` or `requirements.txt` the analyzer can detect.
2. Write hidden tests under `evaluation/datasets/<dataset>/<name>/`. They fail on the
   bug and pass once it is fixed. Keep them out of `sample_repos/`.
3. Write the reference fix as a `git diff` against the sample and save it as
   `gold.patch` next to the hidden tests.
4. Add the case to the dataset YAML and bump the dataset's `version`, because stored
   results are tied to the file's hash.
5. Run `devagent eval validate --gold`. The case must report `OK`.

## Running a benchmark

Run it in the worker container, which has the database, Redis and the sandbox:

```bash
docker compose exec worker devagent eval validate --gold
docker compose exec worker devagent eval run --model <model> --label baseline
docker compose exec worker devagent eval run --model <model> --label no-debug-loop --max-fix-attempts 0
docker compose exec worker devagent eval report                       # list runs
docker compose exec worker devagent eval report <run-id> --compare <other-run-id> > evaluation/reports/<name>.md
```

Other options: `--case <id>` (repeatable), `--repeats N` and `--concurrency N`.

- Every case is a normal agent run; its trace is at `/runs/<id>` in the dashboard.
- Evaluation runs stop at `awaiting_approval` and are never approved or published.

With `--model scripted`, only cases that have a recorded cassette run. The rest are
listed as "not run". A scripted run shows that the harness works; it does not measure
a model.

## Metrics

All numbers come from stored `eval_results` rows:

- **Resolved:** the patch applies to a fresh checkout, all F2P tests pass, no P2P test
  regresses, and (for adversarial cases) no injected action happened.
- **Patch applied, reproduction test created, regression-free** (of applied patches).
- **Debug retries, steps, wall clock, tokens, cost:** mean retries; medians for the
  rest, because they are skewed.
- **Failure taxonomy:** environment, localization, reproduction, incorrect fix,
  regression, invalid patch, budget exceeded, timeout, infra.

Rates show a 95% Wilson score interval. The starter set is small, so intervals are
wide: do not read small differences between runs as real.

## Results so far

[`evaluation/reports/starter-scripted-ablation.md`](../evaluation/reports/starter-scripted-ablation.md)
is the stored report of a scripted run. The slugger case is resolved with the debug
loop (1/1) and not without it (0/1, budget exceeded after the first wrong fix). No
real model has been benchmarked yet.
