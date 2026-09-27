# Progress

Build follows the phased plan in the spec (Section 16), one stacked pull request per phase.

| Phase | Name | Status |
|------:|------|--------|
| 0 | Foundations | ✅ Done |
| 1 | Data model, run API, live events | ✅ Done |
| 2 | Safe cloning & repository analysis | ✅ Done |
| 3 | Docker sandbox | ✅ Done |
| 4 | Tools | ✅ Done |
| 5 | LLM layer, budgets, prompt structure | ✅ Done |
| 6 | Agent loop | ✅ Done (real-LLM demo pending an API key) |
| 7 | Frontend dashboard | ✅ Done |
| 8 | GitHub auth, approval, PR creation | ✅ Done (live PR pending a token and test repo) |
| 9 | Evaluation framework | ✅ Done (real-model numbers pending an API key) |
| 10 | MCP server, hardening, docs, deployment | ✅ Done (real-model benchmark and live PR pending credentials) |

## Phase 0: Foundations (2026-09-27)

### Done
- Monorepo layout; one uv-managed Python 3.12 project; import-linter contracts for the
  dependency rule (ADR 0002).
- Tooling: ruff (lint + format), mypy strict on all Python packages and tests,
  pytest (+asyncio), pre-commit; pnpm, ESLint (zero warnings), Prettier, Vitest, TS strict.
- Backend: typed settings (`backend/config.py`), structlog JSON logging with secret
  redaction (`backend/logging_setup.py`), error envelope (`backend/errors.py`),
  request-id middleware, `/health` readiness (Postgres + Redis) and `/health/live`.
- Worker: arq worker (`python -m backend.worker`) with heartbeat healthcheck
  (`--check`) and a `ping` diagnostic job proving queue wiring.
- Frontend: Next.js 16 App Router, Tailwind 4, dark by default; home page shows
  live system status from `/health`, with unreachable/degraded/empty states.
- Docker: backend image (api + worker), frontend standalone image, `compose.yaml`
  with healthchecks on all five services. Non-root users in app images.
- CI: GitHub Actions jobs for backend, frontend and a compose-based integration job.
- ADRs 0001–0007.

### Verified (see phase report for output)
- `docker compose up --build --wait`: all five services healthy.
- `/health` returns 200 with DB and Redis `ok`; returns 503 `degraded` with Redis stopped.
- 26 unit/security tests, 2 integration tests, 9 frontend tests pass; ruff, mypy,
  import-linter, ESLint, Prettier, tsc clean.

### Known issues / deferred
- shadcn/ui, TanStack Query, Recharts, diff renderer: added with the dashboard (Phase 7).
- OpenTelemetry spans: added with the run pipeline (Phase 1/6); only structlog now.
- Alembic, models, migrations service in Compose: Phase 1.
- Worker has no Docker socket yet; how it gets Docker access is an open decision
  (ADR 0007, Phase 3).
- `config/` (pricing, command policy, defaults) is created in the phases that use it.
- Image builds behind a TLS-intercepting proxy need `DEVAGENT_BUILD_CA_FILE` (see `.env.example`).

## Phase 1: Data model, run API, live events (2026-09-27)

### Done
- `core/`: run state machine (`RunStatus`, transition table) and typed event payloads,
  dependency-free (ADR 0008).
- `database/`: SQLAlchemy models for all 18 spec tables; Alembic migration `0001`;
  `python -m database.migrate` entrypoint; `migrate` one-shot service in Compose.
- Event log with gap-free per-run `seq` (row-locked counter), Redis pub/sub wake-ups
  after commit (ADR 0009).
- API: repositories (create/list/get), runs (create/list/get/cancel/steps), SSE
  `/api/v1/runs/{id}/events` with `Last-Event-ID` / `?after=` resume. Docs in `docs/api.md`.
- Worker: `execute_run` job (idempotent), `DbRunRecorder`, and `DryRunExecutor`: a
  clearly labeled synthetic walk of the state machine including one debug loop.
- Cooperative cancellation; no events after a terminal status.

### Verified (see phase report)
- Migrations upgrade → `alembic check` → downgrade → upgrade on a fresh database.
- 40 concurrent event appends get seq 1..40.
- Live test: dry run streamed over SSE, dropped after 6 events, resumed with
  `Last-Event-ID: 6`, combined ids contiguous; full replay identical; cancel closes stream.
- 74 unit/security tests + 12 integration tests pass; the integration suite passed 8 runs in a row.

### Known issues / deferred
- Budgets are validated and stored in the run config snapshot but not enforced until Phase 5.
- Cancel is cooperative (checked at every step boundary); killing the sandbox
  container on cancel comes with the sandbox (Phase 3).
- `users`, `github_credentials`, approvals, PR, tool/LLM/test and eval tables exist but
  are not written yet; they are filled by Phases 4–9.
- No run wall-clock timeout (`timed_out`) yet: Phase 5 (budgets).
- Enum CHECK constraints are not covered by `alembic check`; see ADR 0008.
- Fixed a build bug found here: uv reused a cached wheel of the project across source
  changes, so images could run stale code. The Dockerfile now installs the project with
  `--no-cache`.

## Phase 2: Safe cloning & repository analysis (2026-09-27)

Batmxn asked for all remaining phases to run without stopping; each phase still gets
its own draft PR, report and verification.

### Done
- `workspace/`: `GitCloner` with the hardening in ADR 0010 (no hooks, no host config,
  symlinks as files, no submodules/LFS, URL allowlist, ref validation, timeout, size and
  file-count limits enforced during and after fetch, token never persisted);
  `copy_local_repository` for sample repos.
- `agent/analysis/`: bounded tree scanner, `PythonAdapter`, `StaticRepositoryAnalyzer`
  producing a `RepoProfile` with evidence (ADR 0011).
- `sample_repos/`: six buggy repos covering different layouts, plus the adversarial
  `notebook` repo with injections in its README, code comment and CI file.
- The backend image now uses `python:3.12-bookworm`, which includes git.
- New settings: workspace root, repo byte/file limits, clone timeout.

### Verified
- Clone security tests run against a real smart-HTTP git server (`git http-backend`):
  host hooks don't run, symlinks are neutralized, submodules aren't fetched, an
  oversized repo is rejected (file count) and an oversized download is killed (bytes),
  option-injection refs are rejected, the token is sent but never written to disk.
- The analyzer reports pytest + the right install/test commands for all 7 sample repos.

### Known issues / deferred
- GitHub API pre-check of repo size before cloning is not implemented; limits are
  enforced while fetching instead.
- Clone is not yet called from a run; the agent loop wires it in (Phase 6).

## Phase 3: Docker sandbox (2026-09-27)

### Done
- `sandbox/docker_sandbox.py`: one container per command from the pinned sandbox image.
  It runs as non-root uid 10001 with all capabilities dropped, `no-new-privileges`, a
  read-only root fs, a `/tmp` tmpfs, CPU, memory (no swap) and PID limits, `init`, a
  capped log driver, and an explicit environment. Network is `none` except for the
  install profile. Output is captured with head and tail kept, and a 64 MB flood kills
  the command. Timeouts kill the container, cancellation removes it, and `kill_run`
  and `reap` clean up by label (ADR 0012).
- Per-run layout: `repo/` → `/workspace`, `env/` (virtualenv, writable only during
  install) → `/env`, `reports/` → `/reports`. In Compose these are subpaths of the
  `devagent-workspaces` volume.
- `install()` creates the run virtualenv (`--system-site-packages`) and runs the
  analyzer's install commands. `run_tests()` adds `--junitxml` for pytest and parses
  the report with `defusedxml` (`sandbox/junit.py`).
- `config/command_policy.yaml` + `sandbox/policy.py`: argv-only allowlist per profile,
  python limited to `.py` files and allowlisted `-m` modules (no `-c`), pip limited to
  `install` with no custom index, path arguments confined, and argument, timeout and
  output limits.
- `docker/sandbox.Dockerfile`: `python:3.12-slim@sha256:f77ac9e4...` with pinned
  pytest, ruff, mypy, setuptools and wheel. The `sandbox-image` Compose service builds it.
- ADR 0007 decided: `sandbox/docker_proxy.py`, a filtering Docker API proxy
  (`docker-proxy` service, the only holder of the socket, on an internal network with
  the worker). It validates create bodies, allowlists endpoints, allows only
  DevAgent-labelled containers, and refuses `exec`.
- Worker: builds the sandbox at startup, logs the image, reaps old containers, runs a
  `reap_sandboxes` cron every 15 minutes, and has a `sandbox_check` job that probes the
  deployed path end to end.
- New settings: `DEVAGENT_DOCKER_HOST`, `DEVAGENT_SANDBOX_*` and
  `DEVAGENT_COMMAND_POLICY_PATH` (documented in `.env.example`).

### Verified (see phase report)
- Security tests against real containers: no network (only `lo`), worker env doesn't
  leak, uid is non-root, CapEff is 0, NoNewPrivs is 1, the root fs, `/etc` and `/env`
  are read-only, and there's no docker.sock. Timeout, OOM, PID limit and output cap are
  enforced. Policy rejections start no container. Cancel, kill_run and the reaper remove
  containers. An install followed by tests gives a parsed JUnit report.
- The proxy against a real daemon: the sandbox works through it unchanged. 11 kinds of
  unsafe create (privileged, host binds, docker.sock, host network/pid, cap_add, writable
  root, seccomp=unconfined, devices, root user, other image) get 403. Networks, volumes,
  pull, unfiltered list, info, image list and other images get 403. Containers not
  created by DevAgent can't be touched. Exec on a sandbox container gets 403.
  Malformed, chunked and oversized requests get 400.
- The full Compose stack (8 services) is healthy. `sandbox_check` runs through
  worker → proxy → daemon with the workspace on the shared volume.
- The textchunk sample repo installed (`pip install -e .[test]`) and its tests ran in
  the sandbox with a parsed JUnit report.

### Known issues / deferred
- Only the Python install and test path exists, matching the analyzer (v1 scope).
- `pip install -e .` leaves `*.egg-info` in the workspace; diffs will exclude it (Phase 4).
- gVisor (`runsc`) is supported by config but not tested here (not installed).
- A compromised worker could still read or modify other runs' workspaces on the shared
  volume (ADR 0007, Consequences).
- In this development environment, dependency installs reach PyPI through a host
  proxy, so the local `.env` sets `DEVAGENT_SANDBOX_INSTALL_NETWORK=host`. CI and normal
  hosts use `bridge`.

## Phase 4: Tools (2026-09-27)

### Done
- `tools/`: `ToolRegistry` plus the 11 tools from spec 5: `list_tree`, `search_files`,
  `search_text` (ripgrep), `find_symbol`, `find_references` (ast), `read_file`,
  `edit_file`, `create_file`, `run_command`, `run_tests`, `git_diff`. Each has a
  Pydantic input model, JSON schema, capability tag and output limit (ADR 0013).
- The registry returns structured errors (`ok` / `error` / `denied` + stable code) for
  unknown tools, bad arguments, path and policy denials, and tool failures, and logs
  every call through a `ToolCallSink`.
- `backend/services/tool_log.py`: `DbToolCallSink` writes `tool_calls` and
  `code_changes`, stores full output when it was truncated, and emits `tool_call` events.
- Path safety: traversal, absolute paths, NUL, symlink escapes and writes through
  symlinks are rejected. `.git/` is blocked. CI config and lockfiles are protected
  unless the plan names them. Dependency and build files are flagged as sensitive.
- `core/tools.py`: tool enums moved into the dependency-free kernel.
- `ripgrep==14.1.0` wheel pinned as a dependency, so `rg` ships in the image.
- **Security fix:** the sandbox now masks `/workspace/.git` with an empty read-only
  tmpfs. Without it, sandboxed code could write `.git/config` (for example a filter
  driver) that host-side git would execute.

### Verified (see phase report)
- 27 unit tests covering every tool: ambiguous and missing edit rejection, traversal
  and symlink escape rejection, protected, blocked and sensitive paths,
  `.gitignore`-aware listing, capped search and invalid regex, ast definitions and
  references, and a diff with new files and no toolchain noise.
- Postgres integration: tool calls (including denied ones), code changes, full-output
  refs and `tool_call` events are persisted.
- Real sandbox: shell and escape attempts through `run_command` are denied with codes.
  Shell metacharacters reach `ls` as literal arguments. `run_tests` finds a real
  failing test, then the reproduction test, then all green after an `edit_file` fix.

### Known issues / deferred
- The spec 6.1 workspace-size check between steps runs in the orchestrator (Phase 6),
  using `workspace.limits`.
- Repositories whose tests need git metadata (e.g. `setuptools_scm`) see an empty `.git`
  in the sandbox.
- `find_references` is name-based (no type inference), so it can over-report.

## Phase 5: LLM layer, budgets, prompt structure (2026-09-27)

### Done
- `llm/types.py`: neutral request and response types. `llm/anthropic_adapter.py` and
  `llm/openai_adapter.py` use `httpx` with bounded retries on rate limits and server
  errors (ADR 0014).
- `config/model_pricing.yaml` + `llm/pricing.py`: Decimal cost accounting. A model
  without a price entry is refused.
- `llm/budget.py`: `BudgetTracker` with steps, fix attempts, tokens, cost and wall
  clock, all checked before the action. The LLM check is worst case.
- `llm/metered.py`: `MeteredLLMClient` prices, budgets and records every call,
  including errors and refusals. `backend/services/llm_log.py` writes `llm_calls` and
  emits `llm_usage` events.
- `llm/prompting.py`: system policy, then role prompt, then task, then untrusted
  content. `wrap_untrusted` neutralises breakout attempts. `PromptLibrary` versions
  prompts by file and content hash.
- `llm/structured.py`: answers come through a `submit` tool, are validated by Pydantic,
  and invalid answers are retried (3 attempts). Every output carries a rationale.
- `llm/injection.py`: heuristic injection flags for the UI.
- `llm/scripted.py`: `ScriptedLLM` with checked expectations and YAML cassettes.
- `backend/llm_factory.py` + `DEVAGENT_LLM_*` / provider key settings.

### Verified (see phase report)
- 47 new unit tests:
  - both adapters' wire formats with mocked HTTP, retry and no-retry cases, keys never
    in errors;
  - pricing math;
  - each budget limit, and a refused call never reaching the provider;
  - wrapper breakout attempts;
  - injection flags on the adversarial sample repo, with no flags on ordinary issue
    text;
  - structured retries and bounds;
  - cassette expectations.
- Postgres integration: `llm_calls` rows for two attempts plus a budget refusal, with
  exact costs, rationale and `llm_usage` events.

### Known issues / deferred
- No live provider call has been made: no API key is available here. The adapters are
  tested against the documented wire formats.
- The pricing file ships only OpenAI entries (dated 2025-04). Add the models you use,
  with current prices.
- Role prompts for each component and the `budget_exceeded` transition come with the
  orchestrator (Phase 6).

## Phase 6: Agent loop (2026-09-27)

### Done
- `agent/orchestrator.py`: `StateMachineOrchestrator` behind an `Orchestrator`
  interface. It runs clone, analyze repo (install plus baseline suite and lint, format
  and type checks), analyze issue, localize and reproduce, then plan, edit and test
  with a bounded debug loop, then validate and wait for approval (ADR 0015).
- Reproduction first: the new test must fail (not error) on the buggy code before any
  fix. The reproduction step may only add files; other changes are reverted and the
  model gets feedback. The confirmed test is read-only for the editor.
- Success is decided by the orchestrator, not the model. The reproduction test must
  pass, and no test that passed at baseline may fail or disappear (`agent/validation.py`).
- Components with typed Pydantic outputs and versioned prompts in
  `agent/prompts/<component>/v1.md`: issue analyzer, localizer, reproducer, planner,
  editor, debugger and PR writer. Every answer carries a rationale, which is stored on
  its step.
- `ComponentRuntime`: layered prompts, structured answers, and a bounded tool loop with
  a final submit-only turn. Tool output is wrapped as untrusted, and file reads are
  scanned for injection attempts.
- Budgets are enforced before every step, fix attempt and LLM call. Running out gives
  `budget_exceeded`; the wall clock gives `timed_out`. Workspace size is checked
  between steps. Failures carry a code and a failure category for the eval taxonomy.
- Validator and review flags: lint, format and type checks are compared with the
  baseline, and sensitive or protected paths, new dependencies, network calls and
  deleted tests are flagged. The PR body is built from recorded results only
  (`agent/pr_text.py`).
- Recorded on the run: base commit, config snapshot (model, provider, temperature),
  prompt versions, sandbox image ID, final diff and SHA-256, injection flags, running
  token and cost totals. The new `agent_runs.result` field (migration 0002) holds the
  plan, reproduction, validation, review flags and PR text.
- Test runs and per-test results are written to `test_runs` and `test_results`, with
  `test_result` events.
- Worker: `run_agent` composition root. A watcher kills the run's sandbox containers
  and stops the orchestrator when the run is cancelled.
- API: `mode: "agent"` is now the default. New fields: `model` and issue `number`.
  Bundled sample repositories can be registered (`{"sample": "slugger"}`, `GET
  /repositories/samples`). Run responses include the replay fields and the result.
- A `scripted` model provider replays a cassette. Compose maps it to
  `tests/cassettes/slugger_fix.yaml`, so the full pipeline can be shown without a key.

### Verified (see phase report)
- Integration test: a ScriptedLLM run on `sample_repos/slugger` goes through the real
  sandbox and database to `awaiting_approval` with the correct diff, including one
  wrong fix corrected by the debug loop.
- Deployed-stack tests: the same run through API, queue, worker, proxy and sandbox; an
  unusable model fails clearly; cancelling mid-run removes the run's containers and
  closes its steps.
- Orchestrator unit tests with real pytest runs on a fixture repository cover: the happy
  path; the debug loop; a reproduction that passes and is rejected; a reproducer that
  edits existing code; fix-attempt, token, cost, step and wall-clock limits;
  injection flags.

### Known issues / deferred
- No real-LLM run yet (spec acceptance: "a real-LLM run fixes at least one sample
  bug"): it needs an API key and model choice from Batmxn.
- Lint, format and type-check regressions are warnings on the approval screen; they do
  not send the run back to debugging, because the state machine has no validating to
  debugging edge.
- The repository analyzer is static only; there is no LLM summary step (spec: "LLM only
  to summarize").
- An optional LangGraph adapter is not built. The `Orchestrator` interface is the
  extension point.
- OpenTelemetry spans are still not emitted; structured logs carry `run_id` and `step_id`.

## Phase 7: Dashboard (2026-09-27)

### Done
- Pages (Next.js 16, React 19, Tailwind 4, TanStack Query):
  - **Home:** new-run form with a bundled sample or a public GitHub URL, the issue,
    and optional model and budget. Also shows system status and recent runs.
  - **Runs:** history table with a status filter.
  - **Run detail:** live timeline with each step's summary, error and rationale. Stats
    for elapsed time, steps, fix attempts, tokens, cost and model. Tabs for the
    terminal, tool calls, LLM calls, plan and tests. A Cancel button, an injection-flag
    badge, a failure panel, and a reproducibility section (base commit, image, diff
    hash, prompt versions).
  - **Review:** split or unified diff (`react-diff-view`), validation checks, review
    flags, the generated PR description, and Approve or Reject with a comment.
  - **Evaluation:** empty state until Phase 9.
- Every page has loading, empty and error states. The run page shows the live
  connection state and a reconnecting banner.
- Dark by default, with a light toggle that persists and does not flash on load.
- Same-origin proxy `app/api/v1/[...path]` streams API responses, including SSE, to the
  browser (ADR 0016).
- API additions for the pages:
  - `GET /runs/{id}/tool-calls`, `/llm-calls`, `/test-runs` (with per-test results),
    `/diff` (with review flags and validation) and `/approvals`.
  - `POST /runs/{id}/approve`, bound to the diff SHA-256 (409 `stale_diff`), and
    `POST /runs/{id}/reject`.
  - Run summaries include the repository, issue, tokens and fix attempts.
- Terminal output is rendered as text; ANSI colors become CSS classes.
- Playwright smoke test against the running stack, which also captures the README
  screenshots in `docs/images/`. CI runs it in the integration job.

### Verified (see phase report)
- Playwright smoke: a seeded scripted run is followed live in the browser to
  `awaiting_approval`, every tab renders, the diff is reviewed and approved, the theme
  toggle persists, and an unknown run shows an error state.
- Vitest: ANSI parser (including HTML staying text), formatters, the API error
  envelope, the timeline, the terminal, and the SSE hook (dedup after reconnect,
  closing on a final status, the disabled state).
- The deployed-stack integration test covers the new endpoints and the stale-diff
  rejection.

### Known issues / deferred
- Approval stops at `approved`. Opening the pull request is Phase 8.
- The evaluation page has no data until Phase 9.
- Screenshots show a scripted run; a real-model run needs an API key.

## Phase 8: GitHub auth, approval, PR creation (2026-09-27)

### Done
- GitHub OAuth sign-in (`/api/v1/auth/github/login`, `/callback`, `/me`, `/logout`):
  - The state is bound to a sealed cookie and the session is a sealed HttpOnly cookie.
  - Tokens are stored Fernet-encrypted, and deleted on sign-out.
  - When OAuth is configured, run and repository endpoints require a session (ADR 0017).
- Issue import: `GET /repositories/{id}/issues` lists open issues, without pull
  requests. The home page shows them as a picker for GitHub repositories.
- Approval records the approver and queues the worker's `publish_run` job. The job
  opens a **draft** PR:
  - It checks the approved SHA-256, then applies the diff to the base commit's files
    with `git apply` in a temporary directory.
  - It builds blobs, a tree, a commit and the `devagent/issue-<n>-<slug>` branch
    through the Git Data API, so the token never touches a clone or the workspace.
  - The PR body is the generated description; the commit uses the configured bot
    identity.
- Edge cases:
  - Missing push permission and rate limits (with the reset time) are reported.
  - If the base branch moved: unrelated upstream changes still get a PR, based on the
    validated commit and noted in the body; changes to a touched file are refused
    with `base_moved`.
  - Branch-name collisions get a `-N` suffix; binary diffs and diffs that do not
    apply are refused.
- Patch fallback: `GET /runs/{id}/patch` serves a `git am` patch for any run with a
  diff. The run stays `approved` with the reason in `result.delivery` (new
  `creating_pr → approved` transition), and `POST /runs/{id}/publish` retries.
- Dashboard:
  - The nav has "Sign in with GitHub", or the avatar and sign-out.
  - The review page shows the PR link or the patch download with the reason, plus a
    retry button.
  - The run page links the PR.
  - The same-origin proxy now passes cookies, redirects and download headers.
- Logging: structlog and stdlib handlers write to the current `sys.stdout`, so a
  reconfigured process never writes to a stale stream.

### Verified (see phase report)
- Publisher against an in-memory GitHub, with real `git diff` and `git apply`:
  - The exact approved tree is committed on the base commit; deletions, renames and
    modes are kept.
  - A stale hash is refused before any request; no push permission is refused.
  - Moved base: an unrelated change gets a PR, a conflicting change is refused.
  - Branch collisions are handled, and a diff that does not apply is refused.
  - The token only appears in the `Authorization` header, never in logs.
- With a database:
  - Sign-in is required when configured, and forged state and open redirects are
    refused. The stored token is encrypted, and sign-out deletes it.
  - Approval queues publishing and opens the PR with the user's token. A permission
    failure falls back to a patch; a retry then succeeds; a second retry gets 409.
  - With no credentials the run gets a patch.
- On the deployed stack, approving a sample run delivers a patch that `git apply`
  accepts on the sample. Browser smoke test: approve, then download the patch.
- The agent run test checks that a configured GitHub token appears nowhere in the
  workspace, diff, tool outputs or events.

### Known issues / deferred
- No PR has been opened on a real repository yet. That needs an OAuth app or a
  fine-grained PAT, plus a test repository, from Batmxn.
- Only public repositories can be solved: cloning is anonymous (ADR 0017).
- The GitHub App installation token is documented, not implemented.

## Phase 9: Evaluation framework (2026-09-27)

### Done
- Dataset format and loader (`evaluation/dataset.py`, [docs/evaluation.md](docs/evaluation.md)).
  The starter dataset has seven cases, one per sample repository, including the
  adversarial `notebook` case. Hidden tests and gold patches live under
  `evaluation/datasets/starter/`, outside what a run can see.
- Scoring (`evaluation/scoring.py`):
  - The final diff is applied with `git apply` to a fresh checkout, and the hidden
    tests are written on top.
  - A new sandbox installs dependencies and runs only the F2P and P2P node ids;
    outcomes come from JUnit XML.
  - A missing test counts as failing. Adversarial cases also check for injected
    actions.
- Harness (`evaluation/harness.py`):
  - Each case is a normal agent run through `run_agent`, with a full trace in the
    dashboard.
  - Supports repeats and concurrency. Cases without a cassette are skipped for the
    scripted model.
  - Runs are never approved or published.
  - Writes `eval_results` with per-test details (migration 0003).
- Failure taxonomy, Wilson 95% intervals, medians (`evaluation/stats.py`, `metrics.py`).
- Markdown report with configuration, prompt versions, dataset hash, per-difficulty
  and per-case tables, and a side-by-side comparison (`evaluation/report.py`).
- CLI: `devagent eval validate [--gold] | run | report [--compare]`.
- API: `GET /api/v1/evaluation/runs` and `/runs/{id}`. The Evaluation page shows run
  history, resolve rate with intervals, metric cards, a failure chart (Recharts), the
  configuration, and cases linked to their agent runs. Empty state when there is no
  data.
- The datasets ship in the backend image; Compose mounts cassettes at
  `tests/cassettes` so dataset paths resolve in the worker.

### Verified (see phase report)
- `devagent eval validate --gold` in the worker: all 7 cases OK. On the base, F2P is
  0/2 and P2P has 0 regressions; with the gold patch, F2P is 2/2 and P2P has 0
  regressions.
- Scripted benchmark on the deployed stack:
  - With the debug loop, 1/1 resolved (1 retry).
  - Without it (`--max-fix-attempts 0`), 0/1 (`budget_exceeded`).
  - Six cases are listed as not run.
  - Report: `evaluation/reports/starter-scripted-ablation.md`.
- Integration tests against a real sandbox:
  - Scoring separates base, gold, a stale patch (`invalid_patch`) and a patch that
    deletes passing tests (regressions).
  - The harness plus ablation produce the expected stored results, and runs are left
    in `awaiting_approval`.
- Unit tests: dataset validation, JUnit id mapping, Wilson values, taxonomy,
  adversarial detection, summary and report. Browser test: the Evaluation page shows
  the stored runs and links cases to traces.

### Known issues / deferred
- No real-model benchmark yet. It needs an API key and model choice from Batmxn.
  Until then, the only stored numbers are the scripted run's, and they are labelled
  as such.
- Only bundled sample repositories can be cases (ADR 0018). There is no SWE-bench
  Lite adapter.
- Scoring reinstalls dependencies for each case (about 10 s each).

## Phase 10: MCP server, hardening, docs, deployment (2026-09-27)

### Done
- MCP server (`python -m mcp_server`, `devagent-mcp`; ADR 0019) on the official SDK
  (`mcp` 2.x), over stdio.
  - Read-only code tools come from the tool registry, against a git checkout.
  - Run control goes through the REST API.
  - There are no approve, reject or publish tools.
  - API errors come back as tool errors.
- Trace export: `GET /runs/{id}/trace` returns the whole run as one JSON document
  (`devagent-trace/v1`), with an **Export trace** button on the run page.
- Hardening:
  - File listing no longer writes to `.git/info/exclude`: excludes are passed to
    `git ls-files`, so reading a checkout changes nothing in it.
  - `git_diff` is kept out of MCP, because it stages into the index.
- `devagent eval run --max-cost` (spec 13.2). Invalid budget options are reported, not
  raised.
- Docs: `docs/architecture.md` (services, packages, state and sequence diagrams),
  `docs/security.md` (threat model, controls, known gaps), a full `docs/api.md`,
  `docs/deployment.md` (single VM with Compose and TLS), "adding a case" in
  `docs/evaluation.md`, `CONTRIBUTING.md`, and MCP variables in `.env.example`.
- Public README: rendered banner, badges, screenshots from real runs, Mermaid
  architecture and state diagrams, quick start, usage (dashboard, API, MCP,
  benchmark), configuration, security summary, evaluation method, benchmark results
  taken from the generated report, limitations. Also an MIT `LICENSE` file, which the
  README already referenced.

### Verified (see phase report)
- MCP: an in-process client and a real stdio subprocess list and call the tools.
  Checked: path escapes are refused, the user's `.git` is unchanged after reads, run
  control sends the right requests, API errors come back as tool errors, and an
  unreachable API is reported rather than crashing.
- The trace export is checked in the stack test and the Playwright test.
- Fresh clone, then `docker compose up`, then the README demo: the full verification
  ran from a fresh clone of this branch.

### Known issues / deferred
- No real-model run, real-model benchmark or live PR yet: each needs credentials.
- The MCP server supports stdio only. Streamable HTTP would need its own
  authentication.
- OpenTelemetry spans and the LangGraph adapter are future work (README).

