# 0009. Run event log, sequence numbers, and cooperative cancellation

- Status: Accepted
- Date: 2026-09-27

## Context
SSE resume (ADR 0005) needs a total order of events per run with no gaps, even with the
API and worker writing concurrently. Cancellation can arrive while a step is in flight.

## Decision
- **Sequence:** `agent_runs.last_event_seq` is incremented with
  `UPDATE … RETURNING` in the same transaction that inserts the `run_events` row. The
  row lock serializes writers per run, so `seq` is strictly increasing and gap-free;
  `UNIQUE (run_id, seq)` backs it up. The SSE `id` is `seq`.
- **Atomicity:** a status change and its `status_changed` event commit together. Redis
  pub/sub is notified only after commit, and only as a wake-up hint.
- **SSE loop:** subscribe first, then read from Postgres; close the stream once the run is
  terminal and every event up to `last_event_seq` was sent.
- **Cancellation (Phase 1):** `POST /runs/{id}/cancel` moves the run to `cancelled` under
  a row lock. The worker checks the run status under the same lock before every
  transition, step start, event and step finish; once the run is terminal it records
  the in-flight step as `skipped` without emitting an event and stops (any other
  step left `running` by the interruption is closed the same way). **No event is
  ever appended after a terminal status event.**

## Alternatives
- `BIGSERIAL` global id as SSE id: ordered but not contiguous per run, so a client
  could not detect gaps.
- Killing the job with `arq`'s abort: interrupts mid-transaction. Phase 3 adds killing the
  sandbox container on cancel, which bounds how long an in-flight command keeps running.

## Consequences
- Cancel latency is bounded by the longest step (dry run: the step delay; real runs:
  the command timeout until Phase 3 kills the container).
