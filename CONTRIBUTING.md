# Contributing

## Layout and dependency rules
See the spec's monorepo layout and [ADR 0002](docs/decisions/0002-single-python-project-with-import-contracts.md).
`tools/`, `sandbox/`, `llm/` must not import `backend/` or `agent/`; `agent/` must not
import `backend/`. `uv run lint-imports` enforces this.

## Quality bar
- Python: full type hints, `mypy --strict` on every package, ruff lint + format,
  explicit error types (no bare `except`, no blanket `except Exception` outside the
  API's last-resort handler), dependency injection over module globals.
- TypeScript: `strict` + `noUncheckedIndexedAccess`, ESLint with zero warnings, Prettier.
- Logging: `structlog.get_logger(__name__)`; log events are snake_case names with
  key/value context. Secrets are redacted by a processor, but never log them on purpose.
- No placeholder logic. Tests use `ScriptedLLM` (Phase 5) instead of live model calls.

## Before pushing
`uv run pre-commit run --all-files` runs every check CI runs except the build and
integration jobs.

## Decisions
Record significant choices as `docs/decisions/NNNN-title.md` and update `PROGRESS.md`
at the end of each phase.
