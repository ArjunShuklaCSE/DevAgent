# 0005. Server-Sent Events with Last-Event-ID replay for live run progress

- Status: Accepted (implemented in Phase 1)
- Date: 2026-09-27

## Context
The Run page needs a live stream of steps, tool calls and command output. The
client never sends data on that channel; commands (cancel, approve) are REST calls.

## Decision
Use **SSE** at `GET /api/v1/runs/{id}/events`. Every event is persisted to `run_events`
with a per-run monotonically increasing sequence number used as the SSE `id`. On
reconnect the browser sends `Last-Event-ID`; the server replays newer rows from
Postgres, then tails live events (Redis pub/sub as the wake-up signal).

## Alternatives
- WebSockets: bidirectional (not needed), no built-in resume, extra proxy config.
- Polling: simple, but laggy and wasteful for streamed terminal output.

## Consequences
- Postgres is the source of truth for replay; Redis loss only delays delivery.
- Request middleware is pure ASGI (not `BaseHTTPMiddleware`) so streaming works.
