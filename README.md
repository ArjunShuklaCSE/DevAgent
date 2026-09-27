# DevAgent

Autonomous GitHub issue solver: give it a Python repository and an issue; it localizes
the code, reproduces the bug with a failing test, plans and applies a fix, validates it
in an isolated Docker sandbox, and opens a **draft** PR only after you approve the exact diff.

> **Status: Phase 0 of 10 (foundations).** The services, tooling and CI are in place;
> the agent itself is not built yet. See [PROGRESS.md](PROGRESS.md).

## Quick start

Requirements: Docker with Compose v2, and for local development `uv` and Node 22 + pnpm.

```bash
cp .env.example .env            # optional; defaults work for local use
docker compose up --build --wait
curl -s localhost:8000/health   # {"status":"ok", "checks": {"database": …, "redis": …}}
open http://localhost:3000      # system status page
```

| Service    | URL / port              | Health check                          |
|------------|-------------------------|---------------------------------------|
| `web`      | http://localhost:3000   | `GET /healthz`                        |
| `api`      | http://localhost:8000   | `GET /health` (readiness, DB + Redis) |
| `worker`   | —                       | `python -m backend.worker --check`    |
| `postgres` | 127.0.0.1:5432          | `pg_isready`                          |
| `redis`    | 127.0.0.1:6379          | `redis-cli ping`                      |

API docs: http://localhost:8000/docs

## Development

```bash
uv sync                          # Python 3.12 env with dev tools
uv run pre-commit install
uv run pytest                    # unit + security tests (no services needed)
uv run pytest -m integration     # needs `docker compose up`
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports

cd frontend && pnpm install
pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and
[docs/decisions/](docs/decisions/) for architecture decision records.

## Benchmark results

No benchmark has been run yet. Results will appear here only from a generated
report in `evaluation/reports/` (Phase 9).

## License

MIT
