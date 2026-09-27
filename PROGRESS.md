# Progress

Build follows the phased plan in the spec (Section 16). Each phase stops for review.

| Phase | Name | Status |
|------:|------|--------|
| 0 | Foundations | ✅ Done |
| 1 | Data model, run API, live events | ✅ Done |
| 2 | Safe cloning & repository analysis | ✅ Done |
| 3 | Docker sandbox | ✅ Done |
| 4 | Tools | Not started |
| 5 | LLM layer, budgets, prompt structure | Not started |
| 6 | Agent loop | Not started |
| 7 | Frontend dashboard | Not started |
| 8 | GitHub auth, approval, PR creation | Not started |
| 9 | Evaluation framework | Not started |
| 10 | MCP server, hardening, docs, deployment | Not started |

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

## Next
Phase 4: tool registry and tools (file read/search/edit with path safety, test and
command tools through the sandbox, diff), logged to `tool_calls` and `code_changes`.
