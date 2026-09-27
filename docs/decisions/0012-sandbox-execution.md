# 0012. Sandbox execution model

- Status: Accepted (Phase 3)
- Date: 2026-09-27

## Context
Every command the agent runs (tests, reproduction scripts, linters, dependency
installs) executes code from an untrusted repository. Spec 6.3 requires isolation,
limits, no network except for installs, an argv-only command policy, output capture
and cleanup.

## Decision
**One container per command** from the pinned sandbox image
(`docker/sandbox.Dockerfile`: `python:3.12-slim` by digest, with pytest, ruff, mypy
and setuptools pinned). This was chosen over one long-lived container per run with
`docker exec`:
- each command gets its own limits, clean `/tmp`, exit status and OOM flag;
- a timed-out or cancelled command is stopped by removing one container, and nothing
  can linger in the background between commands;
- the Docker proxy can deny `exec` completely (ADR 0007).

The cost is container start-up: a full `ls` round trip (create, start, run, remove)
took 0.145–0.181 s over 10 runs on the development machine. That is
small next to LLM latency.

**Container settings.** Non-root uid 10001. `cap_drop: ALL`, `no-new-privileges`,
read-only root filesystem, `/tmp` tmpfs (256 MB, `nosuid,nodev`), memory limit with swap
disabled, CPU and PID limits, `init: true`, and a json-file log driver capped at 1 MB.
The environment is built from scratch (PATH, HOME=/tmp, LANG, Python and pip flags), so
nothing from the worker leaks in.

**Filesystem layout per run** (on the worker, under `DEVAGENT_WORKSPACE_ROOT/<run>/`):
- `repo/` is mounted read-write at `/workspace`, with its `.git` masked by an empty
  read-only tmpfs (added in Phase 4, see ADR 0013);
- `env/` is the run's virtualenv (`--system-site-packages`, so the image's pinned tools
  stay available). It is mounted at `/env`, writable only during install;
- `reports/` is mounted at `/reports` for JUnit files.

In Compose these are subpaths of the named volume `devagent-workspaces`, shared by the
worker and the sandboxes. Outside Compose they are bind mounts.

**Network.** `none` for everything except the `install` profile. Install uses
`DEVAGENT_SANDBOX_INSTALL_NETWORK` (default `bridge`) and an optional proxy and CA.

**Command policy** (`config/command_policy.yaml`, `sandbox/policy.py`):
- argv lists only, with no shell;
- executables allowlisted per profile (`run`, `install`);
- `python` may run only `.py` files or allowlisted `-m` modules. `python -c` is
  rejected, so executed code always exists as a file in the workspace and the trace;
- `pip` may only `install`, with no custom index, trusted host, target or prefix;
- `find -exec/-delete` are denied;
- path arguments must stay under `/workspace`, `/tmp`, `/reports` or `/env`;
- limits on argument count and length, per-executable timeouts, and output caps that
  keep the head and the tail (test summaries print last).

A violation raises `PolicyViolationError` with a stable code before Docker is touched.

**Output.** The runner attaches before start, so no output is lost, and demultiplexes
stdout and stderr. It keeps up to the cap per stream and kills the container if a
command prints more than 64 MB in total.

**Tests.** pytest runs get `--junitxml=/reports/<uuid>.xml -p no:cacheprovider`. The XML
is parsed with `defusedxml`, with a size cap and truncated messages, and then deleted.

**Cleanup.**
- Each container is removed in a `finally`, which also runs on task cancellation.
- `kill_run(run_id)` removes a run's containers by label.
- A reaper (worker start plus an arq cron every 15 minutes) removes managed containers
  older than `DEVAGENT_SANDBOX_REAP_AFTER_SECONDS`.

## Consequences
- The policy is defence in depth. The container is the boundary: `python script.py` can
  do anything the container allows, which is why the container allows little.
- `pip install -e .` writes `*.egg-info` into the workspace. Phase 4 excludes it from
  diffs.
- Only Python repositories have an install and test path in v1 (matching the analyzer).
- The image is pinned by digest and by tool versions. A run records the image ID the
  worker saw at startup (`sandbox_check` returns it, and Phase 6 stores it on the run).
