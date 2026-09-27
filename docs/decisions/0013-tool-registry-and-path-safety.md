# 0013. Tool registry, path safety and the workspace's git metadata

- Status: Accepted (Phase 4)
- Date: 2026-09-27

## Context
Spec 5 defines 11 tools the agent calls through a registry. Each tool has a JSON
schema, a capability tag and limits. Each call is logged, and file tools must stay
inside the workspace.

## Decision
- **Tools are small classes** (`tools/`): a Pydantic input model (`extra="forbid"`, so
  unknown arguments are errors) that also provides the JSON schema, plus a name,
  description, capability (`read`, `write_workspace`, `execute_sandbox`) and output
  limit. They are pure except for the workspace and the sandbox, which come in through
  `ToolContext` (dependency injection; tests pass a fake sandbox).
- **`ToolRegistry.call` never raises for tool problems.** An unknown tool, invalid
  arguments, a path or policy denial, or a tool error each comes back as a structured
  result (`status` ok/error/denied plus a stable `error_code`). The agent can react, and
  every attempt is logged, denied ones included. What the model sees is truncated per
  tool.
- **Logging goes through a `ToolCallSink` port.** The backend's `DbToolCallSink` writes
  `tool_calls` and one `code_changes` row per file change in one transaction. Output
  longer than the model's view is stored in full under the run directory
  (`output_full_ref`), and a `tool_call` event is emitted for the live stream.
- **Path safety** (`tools/paths.py`):
  - paths are relative, or under `/workspace`;
  - `..`, NUL and over-long paths are rejected;
  - the final real path must stay under the workspace's real path. When the path
    passed through a symlink the error is `symlink_escape`;
  - writes never follow a symlink, even one that stays inside the workspace.
- **Path classes** (`tools/sensitive.py`):
  - `.git/` is blocked;
  - CI configuration and lockfiles are *protected*: writable only when the approved plan
    names the path (`ToolContext.allowed_protected_paths`);
  - dependency manifests, build and test configuration are *sensitive*: writable, and
    flagged in `code_changes.is_sensitive_path` for the approval screen.
- **Edits.** `edit_file` is an exact, single-occurrence replacement. Zero or several
  matches fail with the line numbers and nothing is written. The before and after
  SHA-256 and a unified diff are recorded. `create_file` opens the file exclusively and
  never overwrites.
- **Search.**
  - `search_text` uses ripgrep, pinned through the `ripgrep==14.1.0` wheel so the binary
    ships in the image without an apt step. It runs with `--no-config`, a size cap and
    vendored directories excluded, and respects `.gitignore`. Rust regex has no
    catastrophic backtracking.
  - `find_symbol` and `find_references` use Python's `ast`. No code runs, and files
    that don't parse (syntax errors, very deep nesting) are skipped.
- **Listing and diffs** come from git (`ls-files --exclude-standard`, and `diff --cached`
  against the base after `add --all` into the workspace's scratch index), with no
  external diff or textconv. Toolchain artifacts (`*.egg-info`, `__pycache__`, caches)
  are added to `.git/info/exclude`.

## Security finding fixed in this phase
Host-side git runs on a tree that sandboxed code can write. If a sandbox command could
write `/workspace/.git/config`, it could define a filter driver or `core.fsmonitor`
command, and the worker's next `git add` would execute it outside the sandbox. Our
hardened git flags override some of these settings, but not every possible driver. The
sandbox now mounts an **empty read-only tmpfs over `/workspace/.git`**, so repository
code never sees or changes the real git metadata
(`tests/security/test_sandbox_isolation.py::test_git_metadata_is_hidden_from_the_sandbox`).
With that in place, workspace git calls can set `safe.directory` to the exact workspace
path. That is needed when a root worker in development runs git on a tree owned by the
sandbox user.

## Consequences
- Repositories whose tests need git metadata (for example `setuptools_scm` versions)
  see an empty `.git` in the sandbox. That is acceptable for v1, and noted in the
  limitations.
- Adding a tool means adding one class and registering it in `tools/defaults.py`. Its
  schema, logging and error handling come from the registry.
