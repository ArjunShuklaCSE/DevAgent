# 0011. Static repository analysis with pluggable language adapters

- Status: Accepted
- Date: 2026-09-27

## Context
The spec wants the Repository Analyzer to be "primarily static detection; LLM only to
summarize", and adding a language (v2: JS/TS) to be a new adapter, not a rewrite.

## Decision
- `agent/analysis/scan.py` builds a bounded, read-only view of the tree: skips VCS,
  virtualenv and vendored directories, never follows symlinks, caps file reads at 512 KB.
- A `LanguageAdapter` protocol (`applies`, `analyze`) produces a `RepoProfile`: package
  manager, dependency files, test framework, install/test/lint/format/typecheck commands
  as argv lists, entry points, key directories, and **evidence** for each claim.
- The analyzer ranks languages by file count and uses the first adapter that applies.
  v1 ships `PythonAdapter`; other languages return `supported=false` with a reason.
- Lint/format/typecheck commands are only emitted when the project configures that tool.
  The analyzer never invents a check the project does not use.
- If a pytest project doesn't declare pytest, `pip install pytest` is appended to the
  install commands so the sandbox can run the tests.

## Consequences
- Detection is deterministic and unit-tested against every sample repo.
- An LLM summary (Phase 6) consumes the profile; it cannot change the commands.
