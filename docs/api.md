# DevAgent API

Base URL `http://localhost:8000`. Interactive OpenAPI docs: `/docs`.
All errors use one envelope:

```json
{ "error": { "code": "not_found", "message": "Run not found", "details": { "run_id": "…" } } }
```

## Health
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Readiness: Postgres and Redis. 200 `ok` or 503 `degraded`. |
| GET | `/health/live` | Liveness, with no dependency checks. |

## Authentication
When a GitHub OAuth app is configured (`DEVAGENT_GITHUB_CLIENT_ID` and `_SECRET`),
every repository, run and evaluation endpoint needs the session cookie and answers 401
`unauthenticated` without it. With no OAuth app (single-user local mode) they are open,
and Compose binds the API to localhost.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/auth/me` | `{"oauth_enabled", "github_token_configured", "user"}` (`user` is null when signed out). |
| GET | `/api/v1/auth/github/login` | `?next=/path`. Redirects to GitHub with a state bound to a sealed cookie. |
| GET | `/api/v1/auth/github/callback` | GitHub redirects here. Stores the encrypted token and sets the session cookie. |
| POST | `/api/v1/auth/logout` | Deletes the stored token and clears the session. 204. |

## Repositories
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/repositories` | `{"url": "https://github.com/owner/name"}` or `{"sample": "slugger"}`. Idempotent. 201. |
| GET | `/api/v1/repositories` | `?limit=&offset=` → `{items,total,limit,offset}` |
| GET | `/api/v1/repositories/samples` | Names of the bundled sample repositories. |
| GET | `/api/v1/repositories/{id}` | |
| GET | `/api/v1/repositories/{id}/issues` | Open issues from GitHub (pull requests excluded). `?limit=`. |

## Runs
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/runs` | Create and queue a run (body below). 201. |
| GET | `/api/v1/runs` | `?status=&repository_id=&limit=&offset=`, newest first. |
| GET | `/api/v1/runs/{id}` | Issue, config snapshot, counters, `result` (plan, reproduction, validation, PR text, delivery). |
| POST | `/api/v1/runs/{id}/cancel` | Optional `{"reason": "…"}`. Kills the run's sandbox containers. 409 if already final. |
| GET | `/api/v1/runs/{id}/steps` | One step per state, with timings, rationale and output. |
| GET | `/api/v1/runs/{id}/events` | **SSE** stream (below). |
| GET | `/api/v1/runs/{id}/tool-calls` | Every tool call: input, truncated output, status, duration. |
| GET | `/api/v1/runs/{id}/llm-calls` | Every model call: model, prompt version, tokens, cost, latency, rationale. |
| GET | `/api/v1/runs/{id}/test-runs` | Test executions with per-test results. |
| GET | `/api/v1/runs/{id}/diff` | Final diff, its SHA-256, review flags, validation report. |
| GET | `/api/v1/runs/{id}/trace` | The whole run as one JSON document (`devagent-trace/v1`), served as a download. |
| POST | `/api/v1/runs/{id}/approve` | `{"diff_sha256": "…", "comment": "…"}`. 409 `stale_diff` if the hash does not match. Queues publishing. |
| POST | `/api/v1/runs/{id}/reject` | `{"comment": "…"}` |
| GET | `/api/v1/runs/{id}/approvals` | Decisions with their diff hashes. |
| POST | `/api/v1/runs/{id}/publish` | Retry opening the PR for an approved run. |
| GET | `/api/v1/runs/{id}/pull-request` | The draft PR, once opened. |
| GET | `/api/v1/runs/{id}/patch` | The diff as a `git am` patch (fallback when no PR can be opened). |

Run body:

```json
{
  "repository_id": "…",
  "issue": {"title": "…", "body": "…", "number": 3},
  "model": "gpt-4.1-mini",
  "budget": {"max_steps": 40, "max_fix_attempts": 3, "max_tokens": 400000,
             "max_cost_usd": "2.00", "command_timeout_seconds": 300, "wall_clock_seconds": 1800}
}
```

- `model` must have an entry in `config/model_pricing.yaml`. If it is left out, the
  server default (`DEVAGENT_LLM_MODEL`) is used.
- `"model": "scripted"` replays a recorded cassette: no key is needed (demo and tests).
- `"mode": "dry_run"` walks the state machine with synthetic steps, marked
  `synthetic: true`, to exercise the event pipeline.

## Evaluation
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/evaluation/runs` | `?limit=` → stored benchmark runs with summary metrics, newest first. |
| GET | `/api/v1/evaluation/runs/{id}` | Summary plus per-case results. Runs are started with `devagent eval run` ([evaluation.md](evaluation.md)). |

### Example
```bash
REPO=$(curl -s -X POST localhost:8000/api/v1/repositories \
  -H 'content-type: application/json' -d '{"sample":"slugger"}' | jq -r .id)

RUN=$(curl -s -X POST localhost:8000/api/v1/runs -H 'content-type: application/json' \
  -d "{\"repository_id\":\"$REPO\",\"model\":\"scripted\",
       \"issue\":{\"number\":3,\"title\":\"slugify crashes on titles without letters\"}}" \
  | jq -r .id)

curl -N localhost:8000/api/v1/runs/$RUN/events                       # live stream
curl -N -H 'Last-Event-ID: 10' localhost:8000/api/v1/runs/$RUN/events  # resume after event 10

SHA=$(curl -s localhost:8000/api/v1/runs/$RUN/diff | jq -r .sha256)  # once awaiting_approval
curl -s -X POST localhost:8000/api/v1/runs/$RUN/approve -H 'content-type: application/json' \
  -d "{\"diff_sha256\":\"$SHA\"}"
curl -s localhost:8000/api/v1/runs/$RUN/patch -o fix.patch          # sample repos have no GitHub remote
curl -s localhost:8000/api/v1/runs/$RUN/trace -o trace.json
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
