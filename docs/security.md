# Security model

DevAgent runs code it has never seen, from repositories and issues written by
strangers, under the direction of a language model that can be manipulated. This page
names the threats, the controls against each one, and the gaps that remain.

The main principle: **the model is never trusted with a capability it could misuse.**
Prompt-injection defences reduce how often the model is fooled. The capability design
limits what a fooled model can do.

## Assets

- GitHub tokens (OAuth user tokens, an optional fine-grained PAT) and LLM API keys.
- The host and the other services: Postgres, Redis, the API, the Docker daemon.
- The target repository's default branch. Nothing may land there without a person's
  approval.
- The integrity of the run record: the diff a person approves must be the diff that
  ships.

## Threats and controls

### 1. A malicious repository (code, tests, build files, git metadata)

| Control | Where |
| --- | --- |
| The clone never runs repository code: no hooks (`core.hooksPath=/dev/null`), no fsmonitor, no submodules, no LFS smudge, file and ext protocols disabled, host git config ignored. Shallow fetch, HTTPS allowlist, size and file-count limits. | `workspace/clone.py`, [ADR 0010](decisions/0010-safe-cloning.md) |
| Symlinks are checked out as plain files (`core.symlinks=false`), so no path escapes the workspace. | ADR 0010 |
| Repository code runs only in sandbox containers: one per command, non-root, `cap_drop: ALL`, `no-new-privileges`, read-only root filesystem, tmpfs `/tmp`, memory (no swap), CPU and PID limits. The environment is built from scratch. | `sandbox/docker_sandbox.py`, [ADR 0012](decisions/0012-sandbox-execution.md) |
| No network for any agent command or test run (`--network none`). The network is on only for the dependency install step. | ADR 0012 |
| The workspace's real `.git` is hidden from the sandbox behind a read-only tmpfs, so sandboxed code cannot plant a git filter that the worker would later run. | [ADR 0013](decisions/0013-tool-registry-and-path-safety.md) |
| JUnit XML from the sandbox is parsed with `defusedxml`, with a size cap and truncated messages. | `sandbox/junit.py` |
| Workspace size is re-checked between steps, and each command's output is capped. | `agent/orchestrator.py`, `sandbox/output.py` |
| The worker never holds the Docker socket. A filtering proxy allows only sandbox-shaped containers of the sandbox image and refuses `exec`, image builds and pulls, privileged mode, host namespaces, devices and host mounts. | `sandbox/docker_proxy.py`, [ADR 0007](decisions/0007-worker-docker-access.md) |

### 2. A malicious issue, or injected instructions in repository text

| Control | Where |
| --- | --- |
| Layered prompts: system policy, then role, then task, then untrusted content in `<untrusted source=… path=…>` wrappers. The policy says wrapped text is data. Wrapper tags inside the content are escaped, so data cannot close its own wrapper. | `llm/prompting.py`, [ADR 0014](decisions/0014-llm-layer-budgets-and-prompts.md) |
| Heuristic flags for override attempts, exfiltration, CI edits, new dependencies, network calls and remote changes. They are shown as a warning badge on the run, not relied on. | `llm/injection.py` |
| The adversarial `notebook` sample (injections in its README, a code comment and the issue) is in the integration tests and the benchmark. There, success also requires that no injected action happened. | `sample_repos/notebook`, `evaluation/scoring.py` |

### 3. Compromised or wrong model output

| Control | Where |
| --- | --- |
| **Capability design.** The model's tools can read the workspace, edit files inside it and run allowlisted commands in the sandbox. None of them can push, open a PR, call GitHub, reach the network during execution, read secrets or leave the workspace. | `tools/`, [ADR 0013](decisions/0013-tool-registry-and-path-safety.md) |
| Path safety on every file tool: relative paths only, no `..`, the real path must stay in the workspace, and writes never follow symlinks. `.git/` is blocked; CI configs and lockfiles are writable only when the plan names them. | `tools/paths.py`, `tools/sensitive.py` |
| Command policy: argv lists only, no shell, allowlisted executables per profile, no `python -c`, `pip` limited to plain installs, path arguments confined. | `config/command_policy.yaml`, `sandbox/policy.py` |
| Structured output only. Every response is validated against a Pydantic schema, with bounded retries. | `llm/structured.py` |
| The orchestrator judges; the model proposes. A reproduction test must fail before a fix and pass after, and no test that passed at baseline may fail. These are decided from parsed test results. | `agent/orchestrator.py`, `agent/validation.py` |
| Budgets are checked before every step, fix attempt and model call (worst-case token estimate), and the run has a wall-clock limit. | `llm/budget.py` |
| The review screen highlights sensitive and protected paths, new dependencies, network or subprocess calls, and deleted tests. | `agent/validation.py::review_flags` |

### 4. Secret leakage

| Control | Where |
| --- | --- |
| Tokens and keys live only in the `api` and `worker` process environments. They are never written to the workspace, passed to the sandbox, or included in prompts. The integration test checks that a configured GitHub token appears nowhere in the workspace, diff, tool output or events. | `tests/integration/test_agent_run.py` |
| OAuth tokens at rest are Fernet-encrypted (`DEVAGENT_SECRET_KEY`) and deleted on sign-out. Session and OAuth-state cookies are sealed, HttpOnly and SameSite=Lax, and Secure on https. | `backend/crypto.py`, `backend/services/auth.py` |
| structlog redacts tokens, keys, `Authorization` headers and URL credentials in every log line. | `backend/logging_setup.py` |
| PRs are built through the GitHub Git Data API from the approved diff. The token never touches a clone. | `backend/github/publisher.py`, [ADR 0017](decisions/0017-github-auth-and-pull-requests.md) |

### 5. Shipping something nobody approved

| Control | Where |
| --- | --- |
| Only backend code can write to GitHub, and only for a run in `approved`. Only a recorded human decision reaches that state. | `core/run_status.py` |
| Approval is bound to the diff's SHA-256. A stale hash is refused, and the publisher checks the hash again before building the commit. | `backend/services/runs.py`, `backend/github/publisher.py` |
| PRs are always drafts. DevAgent never merges. | `backend/github/client.py` |
| The MCP server has no approve, reject or publish tool, and evaluation runs are never approved. | `mcp_server/server.py`, [ADR 0018](decisions/0018-evaluation-harness.md) |

## Known gaps

- **Install-time network.** Dependency installs get the default bridge network, or an
  operator-configured proxy (`DEVAGENT_SANDBOX_INSTALL_PROXY`). There is no built-in
  allowlist of package indexes. A malicious `setup.py` could reach the internet during
  install (though not the other services: install containers are not on the Compose
  networks).
- **Container isolation is the kernel's.** Docker shares the host kernel. For stronger
  isolation, set `DEVAGENT_SANDBOX_RUNTIME=runsc` with gVisor installed, or run on
  rootless Docker. Neither is the default.
- **No per-container disk quota.** `--storage-opt size=` depends on the storage driver
  and is not set. Workspace size is checked between steps and `/tmp` is a capped tmpfs,
  but a single command can fill the workspace volume until it finishes.
- **The worker is trusted.** A compromised worker could create sandbox-shaped
  containers and read other runs' workspaces on the shared volume (ADR 0007).
- **Single-user by design.** Without an OAuth app configured, the API has no
  authentication. Compose binds it to localhost; do not expose it without configuring
  sign-in ([deployment.md](deployment.md)).
- **Public repositories only for solving.** Cloning is anonymous, so a private
  repository's token would otherwise sit in the workspace clone.
- **Heuristics are heuristics.** Injection flags and review flags can miss things. The
  person approving the diff is the final control, and the review screen is built for
  that person.
