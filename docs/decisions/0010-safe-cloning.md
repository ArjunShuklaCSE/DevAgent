# 0010. Safe cloning in the worker

- Status: Accepted
- Date: 2026-09-27

## Context
Cloning untrusted repositories must never execute their code or leak credentials
(spec 6.4, 6.5). Git can run code via hooks, filters (LFS), `core.fsmonitor`,
submodule URLs (`ext::`), and host-level config; symlinks in a checkout can point
outside the workspace.

## Decision
`workspace/clone.py` runs git as argv lists with:
- a scrubbed environment, a throwaway `HOME`, `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null`, so host config cannot inject hooks or helpers;
- `-c core.hooksPath=/dev/null`, empty `init.templateDir`, `core.fsmonitor=false`,
  `protocol.file.allow=never`, `protocol.ext.allow=never`, no submodule recursion,
  LFS filters blanked plus `GIT_LFS_SKIP_SMUDGE=1`;
- **`core.symlinks=false`**: symlinks are checked out as regular files containing the
  target path, so no path inside the workspace resolves outside it;
- `init` + `fetch --depth=1 origin <ref>` + `checkout --detach FETCH_HEAD`, so a branch,
  tag or full SHA can be pinned; refs are validated against option injection;
- an HTTPS URL allowlist (`https://github.com/` by default);
- a wall-clock timeout, `.git` size polled during the fetch (killed early when over the
  limit), and file-count/byte limits enforced again after checkout;
- a token, when needed, passed as an `http.<host>.extraheader` through `GIT_CONFIG_*`
  environment variables for that process only, never written to `.git/config`.

Local sample repositories are copied (limits checked first) and committed to create a
real base commit.

## Alternatives
- GitHub tarball download API: no git at all, but loses exact SHA semantics for
  arbitrary refs and still needs extraction hardening. Possible future optimization.
- `--storage-opt`-style quotas for the clone directory: filesystem-dependent.

## Consequences
- Repositories that rely on symlinks behave differently in the sandbox. This is rare for
  Python projects and is documented as a limitation.
- The backend image uses `python:3.12-bookworm`, which ships git, instead of `-slim` plus
  an apt step.
