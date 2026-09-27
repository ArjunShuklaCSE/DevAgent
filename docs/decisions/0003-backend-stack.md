# 0003. Backend stack: FastAPI, Pydantic v2, SQLAlchemy 2 async, PostgreSQL 16

- Status: Accepted
- Date: 2026-09-27

## Context
The API serves REST + SSE, validates many structured payloads (LLM outputs, tool
inputs), and stores relational run history plus semi-structured tool payloads.

## Decision
- **FastAPI + Pydantic v2** for async request handling, typed models shared with the
  agent's structured outputs, and generated OpenAPI docs at `/docs`.
- **SQLAlchemy 2.0 (async, asyncpg) + Alembic** for typed ORM models and reversible
  migrations (Phase 1).
- **PostgreSQL 16**: relational integrity for runs/steps/events, `JSONB` for tool
  inputs/outputs, and ordered `run_events` for SSE replay.
- Settings via **pydantic-settings** (`DEVAGENT_*` env vars), frozen, connection
  strings as `SecretStr`.
- App built by a factory (`create_app(settings, resource_factory)`) so tests inject
  fakes instead of patching globals.

## Alternatives
- Django/DRF: heavier, sync-first ORM. Litestar: fine, smaller ecosystem.
- SQLModel: thinner layer, weaker typing of relationships and less control.

## Consequences
- Async all the way down; blocking calls (Docker SDK, git) must go to threads or the worker.
