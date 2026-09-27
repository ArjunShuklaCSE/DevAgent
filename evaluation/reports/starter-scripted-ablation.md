# DevAgent benchmark: starter v1 (debug-loop)

- Evaluation run: `91eea1d7-da11-4f73-a492-dd2b7d9c9a27` (completed)
- Model: `scripted`
- Started: 2026-09-27 13:25 UTC
- Cases scored: 1 (repeats per case: 1)
- Budget: max_steps=40, max_tokens=400000, max_cost_usd=2.00, max_fix_attempts=3, wall_clock_seconds=1800, command_timeout_seconds=300
- Dataset sha256: `3dfde9d2e56ec56a0c0df7d4ecbfc4cd9d8badb4bd915c9f4d6b033c0ff56977`
- Sandbox image: `devagent-sandbox:dev`
- Prompt versions: debugger v1:45725f757074, editor v1:8bbf80ca0eef, issue_analyzer v1:ccbb52a1f19a, localizer v1:17d34dd0ef9b, planner v1:d736455d797c, pr_writer v1:e74477da89c0, reproducer v1:637a83070913
- Not run: kvconfig-equals-in-value, textchunk-last-chunk, tagstore-mutable-default, tzconvert-offset, wallet-exception-type, notebook-adversarial-search (no recorded cassette for this model)

> This run uses the `scripted` model, which replays a recorded cassette. It
> shows that the harness, scoring and report work end to end; it says nothing
> about how well any real model solves issues.

## Results

Rates show the 95% Wilson score interval. With few cases the interval is wide,
so small differences between runs are not meaningful.

| Metric | Value |
| --- | --- |
| Resolved (hidden tests pass, no regressions) | 100% (1/1; 95% CI 21% to 100%) |
| Patch applied to a fresh checkout | 100% (1/1; 95% CI 21% to 100%) |
| Reproduction test created | 100% (1/1; 95% CI 21% to 100%) |
| Regression-free (of applied patches) | 100% (1/1; 95% CI 21% to 100%) |
| Mean debug retries | 1.00 |
| Median steps | 12.0 |
| Median wall clock | 16.2 s |
| Median tokens | 2,100 |
| Mean cost per case | $0.0000 |
| Total cost | $0.0000 |

## By difficulty

| Difficulty | Resolved |
| --- | --- |
| easy | 100% (1/1; 95% CI 21% to 100%) |

## Failure taxonomy

No failures.

## Comparison with no-debug-loop

| Metric | debug-loop | no-debug-loop |
| --- | --- | --- |
| Resolved | 100% (1/1; 95% CI 21% to 100%) | 0% (0/1; 95% CI 0% to 79%) |
| Patch applied | 100% (1/1; 95% CI 21% to 100%) | 0% (0/1; 95% CI 0% to 79%) |
| Mean debug retries | 1.00 | 0.00 |
| Median steps | 12.0 | 8.0 |
| Median tokens | 2,100 | 1,500 |
| Total cost | $0.0000 | $0.0000 |

Budget of no-debug-loop: max_steps=40, max_tokens=400000, max_cost_usd=2.00, max_fix_attempts=0, wall_clock_seconds=1800, command_timeout_seconds=300

## Cases

| Case | Difficulty | Repeat | Outcome | F2P | P2P regressions | Retries | Steps | Wall clock | Tokens | Cost | Agent run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| slugger-empty-title | easy | 0 | resolved | 2/2 | 0 | 1 | 12 | 16.2 s | 2,100 | $0.0000 | `c6b69185-f558-466f-9893-051e54336fbf` |

Each agent run's full trace (steps, tool calls, model calls, test runs) is at
`/runs/<agent run id>` in the dashboard.

