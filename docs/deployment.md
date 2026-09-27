# Deployment: a single VM with Docker Compose

DevAgent is built for one developer or a small team on one machine. This guide runs
it on a Linux VM with the same Compose file used for development.

## 1. The machine

- Linux x86_64 with Docker Engine 24+ and the Compose v2 plugin. Windows hosts are not
  supported.
- At least 4 vCPU, 8 GB RAM and 40 GB disk. The worker runs up to 4 jobs at once; each
  run executes one sandbox command at a time, and each command gets 1 CPU and 1 GB by
  default (`DEVAGENT_SANDBOX_*`).
- Optional, for stronger isolation: install gVisor and set
  `DEVAGENT_SANDBOX_RUNTIME=runsc`.
- The worker talks to Docker only through the `docker-proxy` service. Nothing else
  needs the socket.

## 2. Configure

```bash
git clone https://github.com/ArjunShuklaCSE/DevAgent.git && cd DevAgent
cp .env.example .env
```

In `.env`, set:

| Variable | Why |
| --- | --- |
| `DEVAGENT_ENVIRONMENT=production` | Recorded in logs, so production lines are easy to tell apart. |
| `POSTGRES_PASSWORD` | Replace the development password. |
| `DEVAGENT_SECRET_KEY` | Fernet key: encrypts stored tokens and seals cookies. |
| `DEVAGENT_PUBLIC_WEB_URL` | The https URL people use, e.g. `https://devagent.example.com`. It makes cookies Secure and forms the OAuth callback. |
| `DEVAGENT_GITHUB_CLIENT_ID`, `DEVAGENT_GITHUB_CLIENT_SECRET` | GitHub OAuth app. **Required** before the dashboard is reachable by anyone else: without it the API has no sign-in. |
| `DEVAGENT_LLM_MODEL` and a provider key | The model runs use by default. It needs an entry in `config/model_pricing.yaml`; check prices against the provider before you rely on the cost budget. |
| `DEVAGENT_GITHUB_TOKEN` (optional) | A fine-grained PAT, used for PRs when a user token is not available. |

The OAuth app's callback URL must be `<DEVAGENT_PUBLIC_WEB_URL>/api/v1/auth/github/callback`.

## 3. Start

```bash
docker compose up -d --build --wait
docker compose ps
curl -s localhost:8000/health
```

`migrate` applies database migrations on every start, and `sandbox-image` builds the
sandbox image. Both exit when done.

## 4. Put TLS in front

Compose binds the web (3000) and API (8000) ports to `127.0.0.1` only. Publish **only
the web app** through a reverse proxy that terminates TLS: the dashboard proxies
`/api/v1` to the API itself, including SSE and cookies. For example, with Caddy:

```
devagent.example.com {
    reverse_proxy 127.0.0.1:3000 {
        flush_interval -1   # stream server-sent events without buffering
    }
}
```

Do not publish Postgres, Redis, the API port or the Docker proxy.

## 5. Operate

| Task | Command |
| --- | --- |
| Logs (JSON, secrets redacted) | `docker compose logs -f api worker` |
| Upgrade | `git pull && docker compose up -d --build --wait` (migrations run automatically) |
| Back up the database | `docker compose exec postgres pg_dump -U devagent devagent > devagent.sql` |
| Run the benchmark | `docker compose exec worker devagent eval run --model <model>` ([evaluation.md](evaluation.md)) |
| Remove stale sandbox containers | Automatic: on worker start and every 15 minutes. |

Workspaces live on the `devagent-workspaces` volume and are deleted after each run
unless `DEVAGENT_KEEP_WORKSPACES=true`.

## 6. Limits of this setup

- One worker runs at most 4 jobs at once. Add worker replicas only after checking CPU
  and memory for the extra concurrent sandboxes.
- Dependency installs have network access through the default bridge (or your proxy).
  See [security.md](security.md#known-gaps).
- Single-user by design: there are no roles or teams.
