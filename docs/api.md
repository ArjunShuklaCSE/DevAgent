# DevAgent API

Base URL `http://localhost:8000`. Interactive OpenAPI docs: `/docs`.
All errors use one envelope:

```json
{ "error": { "code": "not_found", "message": "Run not found", "details": { "run_id": "…" } } }
```

## Health
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Readiness: Postgres + Redis. 200 `ok` or 503 `degraded`. |
| GET | `/health/live` | Liveness; no dependency checks. |

## Repositories
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/repositories` | `{"url": "https://github.com/owner/name"}`. Idempotent. 201. |
| GET | `/api/v1/repositories` | `?limit=&offset=` → `{items,total,limit,offset}` |
| GET | `/api/v1/repositories/{id}` | |

## Runs
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/runs` | Create and queue a run. 201. |
| GET | `/api/v1/runs` | `?status=&repository_id=&limit=&offset=` |
| GET | `/api/v1/runs/{id}` | Includes issue, config snapshot, counters. |
| POST | `/api/v1/runs/{id}/cancel` | Optional `{"reason": "…"}`. 409 if already final. |
| GET | `/api/v1/runs/{id}/steps` | Ordered steps. |
| GET | `/api/v1/runs/{id}/events` | **SSE** stream (below). |

Only `"mode": "dry_run"` is accepted until the agent loop exists (Phase 6). A dry run
walks the real state machine with **synthetic** steps: every event carries
`"synthetic": true` and step summaries start with `[dry run]`. It ends in
`awaiting_approval`; cancel it to close it.

## Evaluation
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/evaluation/runs` | `?limit=` → stored benchmark runs with summary metrics (newest first). |
| GET | `/api/v1/evaluation/runs/{id}` | Summary plus per-case results. Runs are started with `devagent eval run` ([evaluation.md](evaluation.md)). |

### Example
```bash
REPO=$(curl -s -X POST localhost:8000/api/v1/repositories \
  -H 'content-type: application/json' -d '{"url":"https://github.com/pallets/click"}' | jq -r .id)

RUN=$(curl -s -X POST localhost:8000/api/v1/runs -H 'content-type: application/json' \
  -d "{\"repository_id\":\"$REPO\",\"issue\":{\"title\":\"Demo\"},\"dry_run_step_delay_ms\":200}" \
  | jq -r .id)

curl -N localhost:8000/api/v1/runs/$RUN/events                       # live stream
curl -N -H 'Last-Event-ID: 10' localhost:8000/api/v1/runs/$RUN/events  # resume after 10
curl -s -X POST localhost:8000/api/v1/runs/$RUN/cancel
```

## Event stream
Each message:
```
id: 12
event: step_started
data: {"seq":12,"run_id":"…","step_id":"…","type":"step_started","created_at":"…","payload":{…}}
```
- `id` is a per-run sequence number starting at 1, contiguous with no gaps.
- Reconnect with the `Last-Event-ID` header (browsers do this automatically) or
  `?after=<seq>` to receive only newer events; the header wins if both are sent.
- `: keepalive` comments are sent when idle (`DEVAGENT_SSE_KEEPALIVE_SECONDS`, default 15).
- The stream closes after the run's final status event. Nothing is emitted after it.

Event types: `status_changed`, `step_started`, `step_completed`, `command_output`,
`tool_call`, `test_result`, `llm_usage`, `error`. Payload schemas are in `core/events.py`.
