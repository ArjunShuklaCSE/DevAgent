# 0007. How the worker gets Docker access

- Status: Accepted (Phase 3)
- Date: 2026-09-27

## Context
The worker starts sibling sandbox containers, so it needs the Docker API. Mounting
`/var/run/docker.sock` into the worker is equivalent to root on the host: anyone who
compromises the worker process (for example through a bug in handling untrusted
repository output) can start a privileged container that mounts `/` and escape.

## Options considered
1. **Raw socket in the worker** (spec default). Simplest; worker compromise is host
   compromise.
2. **Off-the-shelf endpoint proxy** (`tecnativa/docker-socket-proxy`). It allows or
   denies whole API sections (containers, images, exec...). It cannot look inside a
   container create request, so a worker allowed to create containers can still create
   a privileged one with the host filesystem mounted. It narrows the API, but does not
   close the escape.
3. **Filtering proxy that validates request bodies** (chosen). A small proxy of our
   own, in `sandbox/docker_proxy.py`, owns the socket and forwards only what the
   sandbox runner needs, after checking it.
4. **Rootless Docker, a separate daemon or VM for sandboxes, gVisor.** Stronger, but
   they depend on the host. They can be combined with 3 and are documented as
   hardening options.

## Decision
Option 3. The `docker-proxy` service is the only service with the socket. The worker
reaches it at `tcp://docker-proxy:2375` over an `internal` Compose network that nothing
else joins. The proxy:

- allows only ping and version, filtered container lists, container create, and
  start, attach, wait, kill, inspect, logs and remove, plus inspect of the sandbox
  image. `exec`, build, image pull and list, networks, volumes, swarm, plugins,
  secrets, `info` and events are refused;
- validates every create request. It must use an allowed image, carry
  `devagent.managed=true`, set a non-root user, drop ALL capabilities, set
  `no-new-privileges` and a read-only root filesystem, stay within memory, CPU and PID
  limits, use an allowed network and runtime, and mount only the workspaces volume
  (with a relative subpath) or allowed bind roots. It must not set privileged mode, host
  namespaces, devices, sysctls, ports, extra network endpoints or `VolumesFrom`;
- allows container-scoped calls only for containers labelled `devagent.managed=true`,
  checked against the daemon on every call, so the worker cannot stop, attach to or
  inspect Postgres, the API or the proxy;
- forwards each request with `Connection: close`, so every request on a keep-alive
  client connection is checked. `attach` is forwarded as an upgraded raw stream;
- refuses chunked or oversized request bodies.

The runner creates one container per command, so it never needs `exec`.

The proxy runs from the backend image as root, because the socket is root-owned. It
drops ALL capabilities, sets `no-new-privileges`, has a read-only root filesystem, and
has no published ports.

Why not option 2: we evaluated `tecnativa/docker-socket-proxy:0.3.0`. Allowing
`CONTAINERS` + `POST` there also allows privileged creates with host mounts, and that
is the exact escape this ADR is about.

## Consequences
- Worker compromise gives an attacker only what the proxy allows: sandbox-shaped
  containers of the sandbox image, on allowed networks, with the workspaces volume.
  They could read or modify other runs' workspaces on that volume, or use CPU up to the
  limits. They could not reach the host filesystem or other services' containers.
- The proxy itself holds the socket. Its code is small, typed and covered by 41 unit
  tests of the rules and 16 tests against a real daemon
  (`tests/security/test_docker_proxy.py`).
- The Docker socket is mounted without `:ro`. A read-only bind of a socket does not
  limit the API, and we don't want the flag to suggest that it does.
- Adding a Docker feature to the runner means adding it to the proxy allowlist too. That
  is intended.
- Stronger isolation stays available: set `DEVAGENT_SANDBOX_RUNTIME=runsc` when gVisor
  is installed (the proxy allowlist follows it), or run the stack on rootless Docker.
