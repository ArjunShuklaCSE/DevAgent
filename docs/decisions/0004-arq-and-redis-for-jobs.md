# 0004. Background jobs with arq on Redis

- Status: Accepted
- Date: 2026-09-27

## Context
Agent runs take minutes, must survive API restarts, be cancellable, and run in a
process that holds Docker access.

## Decision
Use **arq** on **Redis 7**. The worker is a separate service
(`python -m backend.worker`) on queue `devagent:queue`. Its compose healthcheck is
`python -m backend.worker --check`, which reads the heartbeat key the running worker
refreshes every 10 s, so a hung worker turns unhealthy.

## Alternatives
- Celery: mature but sync-first, more configuration, heavier for one job type.
- Dramatiq / RQ: sync. FastAPI BackgroundTasks: dies with the API process, no retries.

## Consequences
- Async job code shares types and clients with the API.
- arq offers at-least-once delivery only with retries configured; run jobs must be
  idempotent per state transition (Phase 1/6 design constraint).
- Redis runs with AOF persistence so queued jobs survive a restart.
