# Progress

Build follows the phased plan in the spec (Section 16). Each phase stops for review.

| Phase | Name | Status |
|------:|------|--------|
| 0 | Foundations | ✅ Done, awaiting confirmation |
| 1 | Data model, run API, live events | Not started |
| 2 | Safe cloning & repository analysis | Not started |
| 3 | Docker sandbox | Not started |
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
- GitHub Actions has not run yet; it runs when this branch's PR is opened.
- shadcn/ui, TanStack Query, Recharts, diff renderer: added with the dashboard (Phase 7).
- OpenTelemetry spans: added with the run pipeline (Phase 1/6); only structlog now.
- Alembic, models, migrations service in Compose: Phase 1.
- Worker has no Docker socket yet; how it gets Docker access is an open decision
  (ADR 0007, Phase 3).
- `config/` (pricing, command policy, defaults) is created in the phases that use it.
- Image builds behind a TLS-intercepting proxy need `DEVAGENT_BUILD_CA_FILE` (see `.env.example`).

## Next
Phase 1: SQLAlchemy models for all tables + Alembic migrations, runs/steps/events
API, arq wiring for runs, SSE with `Last-Event-ID` replay, and a labeled dry-run job.
