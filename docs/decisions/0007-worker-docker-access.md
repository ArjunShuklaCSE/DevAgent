# 0007. How the worker gets Docker access (to be decided in Phase 3)

- Status: Proposed
- Date: 2026-09-27

## Context
The spec gives the `worker` service Docker access so it can create sibling sandbox
containers. Mounting `/var/run/docker.sock` into the worker is **equivalent to root on
the host**: anyone who compromises the worker process (e.g. via a parsing bug on
untrusted repo output) can start a privileged container and escape.

## Options
1. Raw socket mount (spec default): simplest; worker compromise = host compromise.
2. **Docker socket proxy** (e.g. `tecnativa/docker-socket-proxy`) exposing only the
   container/image endpoints the worker needs, plus worker-side validation that every
   create request carries the sandbox security options. Blocks `privileged`, host
   mounts etc. only if the proxy or a small custom proxy filters request bodies.
3. **Rootless Docker** or a dedicated sandbox daemon/VM for sandbox containers:
   worker compromise stays confined to an unprivileged user or a throwaway VM.
4. gVisor (`runsc`) runtime for sandbox containers as defence in depth on top of 2 or 3.

## Proposed direction
Option 2 for the Compose deployment, with an optional `runsc` runtime flag, and an
honest note in `docs/security.md` about residual risk. Final decision in Phase 3.

## Consequences
Phase 0 does **not** mount the Docker socket into any service yet.
