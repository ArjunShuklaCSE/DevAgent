# 0006. Separate liveness and readiness health endpoints

- Status: Accepted
- Date: 2026-09-27

## Context
The spec asks for `/health` returning DB and Redis status. Orchestrators also need
a check that doesn't flap when a dependency blips.

## Decision
- `GET /health` = readiness: probes Postgres (`SELECT 1`) and Redis (`PING`)
  concurrently, each with a timeout (default 2 s). Returns **200** with
  `status: ok` or **503** with `status: degraded` and a per-dependency `error`.
  Error strings are passed through the log redactor so DSNs never leak.
- `GET /health/live` = liveness: returns 200 if the process serves requests; no I/O.
- Compose uses `/health` for the `api` healthcheck so `web` waits for a working backend.

## Alternatives
- One endpoint that always returns 200 with a body: hides outages from load balancers.

## Consequences
- A Redis outage makes the API unready (correct: runs can't be queued), but not dead.
