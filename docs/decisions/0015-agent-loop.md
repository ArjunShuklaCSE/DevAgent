# 0015. Agent loop: explicit state machine, reproduction first, deterministic judging

- Status: Accepted (Phase 6)
- Date: 2026-09-27

## Context
Spec 4.3 and 4.4 describe the run state machine and nine components. Spec 6.6 sets
budgets. Phase 6 requires a scripted run on a sample repository to reach
`awaiting_approval` with a correct diff. Several points needed an interpretation.

## Decision
- **One orchestrator class walks the state machine** (`StateMachineOrchestrator`,
  behind the `Orchestrator` protocol). Each state is one recorded step; the transition
  table in `core/run_status.py` rejects anything else. LangGraph is not used. The
  protocol is where an adapter could plug in, and none of its types reach tools or
  components.
- **The orchestrator judges; the model proposes.** Test commands run by the
  orchestrator decide success, never the model's own claims. The rules:
  - A reproduction test counts only if it *fails* (not errors) on the base code.
  - A fix counts only if that test passes and no test that passed at baseline fails or
    disappears.
  - The reproduction step may only add files. The editor cannot write the reproduction
    test (`ToolContext.read_only_paths`), so it cannot "fix" the bug by weakening the test.
- **Tool loops are bounded per component** (localizer 12, reproducer 10, editor 12
  rounds). When the rounds run out, the model gets one turn in which it can only submit.
  Only the tools a step needs are offered: the localizer is read-only, and only the
  editor may edit existing files.
- **Budget interpretation.**
  - "Agent steps" are recorded state-machine steps (the rows the UI shows), not model
    turns. Model turns are bounded by the per-component round limits and by the
    token and cost budgets.
  - A fix attempt is one debug-and-retry cycle, so `max_fix_attempts = 0` disables the
    debug loop (the ablation in spec 13.3).
  - Running out of fix attempts, tokens, cost or steps ends the run as
    `budget_exceeded`.
  - The wall-clock limit ends it as `timed_out`. That status is in the state machine
    and is more specific; the reason text names the limit either way.
- **Failures carry a category** (environment, localization, reproduction, incorrect
  fix, budget, timeout, infra), stored in `result.failure`. Phase 9 uses it directly
  for the failure taxonomy.
- **Validation.** Lint, format and type checks run at baseline and again after the fix.
  A check that already failed before is `pre_existing`, not blamed on the change. New
  problems are shown as warnings for the reviewer and do not block, because the spec's
  state machine has no edge from `validating` back to `debugging`.
- **PR text** is written by the model for the prose sections only. Validation results,
  review flags and the trace link are rendered from recorded data, so the description
  cannot claim a check that did not pass.
- **Cancellation.** A watcher on the worker polls the run's status. On cancel it kills
  the run's containers and cancels the orchestrator task, so a long test command stops
  at once rather than at the next step boundary.
- **Key-free demo.** A `scripted` model in the pricing file replays a cassette. It is
  used only when a run asks for it, and runs record `provider: scripted`, so it is
  never mistaken for a real model's results.

## Alternatives
- Letting the model decide when it is done (a single ReAct loop). Rejected: it is
  harder to bound and inspect, and it lets the model grade its own work.
- Counting every model turn as a step. Rejected: with the spec's default of 40 steps,
  a normal run with retries would run out of steps before it runs out of fix attempts.

## Consequences
- Runs are easy to follow in the UI (one step per state) and to replay (prompt versions,
  image ID, base commit and config are all recorded).
- A repository whose baseline suite does not produce a report is rejected early, as an
  environment failure.
