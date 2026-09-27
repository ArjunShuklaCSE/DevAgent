"""Filtering Docker API proxy for the worker (ADR 0007).

The worker needs to start sandbox containers, but raw access to the Docker socket is
root on the host. This proxy is the only thing that talks to the socket. It listens on
TCP inside the Compose network and forwards a request only if:

- the endpoint is one the sandbox runner uses (ping, version, create, start, attach,
  wait, kill, inspect, logs, remove, inspect of the sandbox image, list with the
  ``devagent.managed`` filter); ``exec``, ``build``, networks, volumes, swarm,
  plugins, secrets and everything else are refused;
- a container create request carries the sandbox security settings: allowed image,
  non-root user, all capabilities dropped, ``no-new-privileges``, read-only root
  filesystem, memory/CPU/PID limits, an allowed network and runtime, no privileged
  mode, host namespaces, devices, or host paths outside the allowed roots;
- a request that names a container targets one labelled ``devagent.managed=true``,
  so a compromised worker cannot stop or attach to Postgres or the proxy itself.

Each forwarded request is sent upstream with ``Connection: close``, so every request
on a client connection passes through the checks. ``attach`` upgrades the connection
to a raw stream, which is then piped both ways.

Run with ``python -m sandbox.docker_proxy`` (settings from ``DEVAGENT_PROXY_*``).
"""

import asyncio
import json
import posixpath
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, unquote, urlsplit

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

MAX_HEAD_BYTES: Final = 64 * 1024
MAX_BODY_BYTES: Final = 1024 * 1024
MANAGED_LABEL: Final = "devagent.managed"

_VERSION_PREFIX = re.compile(r"^/v\d+\.\d+(?=/)")
_ID = r"(?P<id>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})"
_SCOPED_ROUTES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("GET", re.compile(rf"^/containers/{_ID}/json$")),
    ("GET", re.compile(rf"^/containers/{_ID}/logs$")),
    ("POST", re.compile(rf"^/containers/{_ID}/(start|kill|wait|attach)$")),
    ("DELETE", re.compile(rf"^/containers/{_ID}$")),
)
_SAFE_SECURITY_OPTS: Final = frozenset({"no-new-privileges", "no-new-privileges:true"})
_FORBIDDEN_HOST_KEYS: Final = (
    "Devices",
    "DeviceRequests",
    "DeviceCgroupRules",
    "VolumesFrom",
    "Links",
    "Sysctls",
    "PortBindings",
    "PublishAllPorts",
    "CgroupParent",
    "OomKillDisable",
)


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVAGENT_PROXY_", extra="ignore", frozen=True)

    listen_host: str = "0.0.0.0"  # noqa: S104 - only reachable on the internal network
    listen_port: int = 2375
    upstream_socket: str = "/var/run/docker.sock"
    allowed_images: list[str] = Field(default_factory=lambda: ["devagent-sandbox:dev"])
    allowed_volumes: list[str] = Field(default_factory=list)
    allowed_bind_roots: list[str] = Field(default_factory=list)
    allowed_networks: list[str] = Field(default_factory=lambda: ["none", "bridge"])
    allowed_runtimes: list[str] = Field(default_factory=lambda: ["", "runc"])
    max_memory_bytes: int = 4 * 1024 * 1024 * 1024
    max_pids: int = 1024
    max_nano_cpus: int = 4_000_000_000
    log_level: str = "INFO"


@dataclass(frozen=True)
class Denied:
    status: int
    reason: str


# ---------------------------------------------------------------------- validation
def validate_create(body: object, settings: ProxySettings) -> list[str]:
    """Return every reason the container create request is unsafe (empty = allowed)."""
    if not isinstance(body, dict):
        return ["body must be a JSON object"]
    host: dict[str, Any] = body.get("HostConfig") or {}
    return [
        *_identity_problems(body, settings),
        *_privilege_problems(host),
        *_isolation_problems(body, host, settings),
        *_limit_problems(host, settings),
        *_mount_problems(host, settings),
    ]


def _identity_problems(body: dict[str, Any], settings: ProxySettings) -> list[str]:
    problems = []
    if body.get("Image") not in settings.allowed_images:
        problems.append(f"image {body.get('Image')!r} is not allowed")
    if (body.get("Labels") or {}).get(MANAGED_LABEL) != "true":
        problems.append(f"label {MANAGED_LABEL}=true is required")
    user = str(body.get("User") or "")
    if not user or user.split(":", 1)[0] in ("0", "root"):
        problems.append("container must run as a non-root user")
    if body.get("Volumes"):
        problems.append("anonymous volumes are not allowed")
    return problems


def _privilege_problems(host: dict[str, Any]) -> list[str]:
    problems = []
    if host.get("Privileged"):
        problems.append("privileged mode is not allowed")
    if host.get("CapAdd"):
        problems.append("adding capabilities is not allowed")
    if "ALL" not in (host.get("CapDrop") or []):
        problems.append("CapDrop must include ALL")
    security_opts = set(host.get("SecurityOpt") or [])
    if not security_opts & _SAFE_SECURITY_OPTS:
        problems.append("no-new-privileges is required")
    if extra := security_opts - _SAFE_SECURITY_OPTS:
        problems.append(f"security options not allowed: {sorted(extra)}")
    if host.get("ReadonlyRootfs") is not True:
        problems.append("read-only root filesystem is required")
    problems += [f"{key} is not allowed" for key in _FORBIDDEN_HOST_KEYS if host.get(key)]
    return problems


def _isolation_problems(
    body: dict[str, Any], host: dict[str, Any], settings: ProxySettings
) -> list[str]:
    problems = []
    for key in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode"):
        value = str(host.get(key) or "")
        if value == "host" or value.startswith("container:"):
            problems.append(f"{key}={value} is not allowed")
    network = str(host.get("NetworkMode") or "")
    if network not in settings.allowed_networks:
        problems.append(f"network {network!r} is not allowed")
    endpoints = (body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
    if set(endpoints) - {network}:
        problems.append("extra network endpoints are not allowed")
    if str(host.get("Runtime") or "") not in settings.allowed_runtimes:
        problems.append(f"runtime {host.get('Runtime')!r} is not allowed")
    return problems


def _limit_problems(host: dict[str, Any], settings: ProxySettings) -> list[str]:
    problems = []
    checks: Sequence[tuple[str, int]] = (
        ("Memory", settings.max_memory_bytes),
        ("PidsLimit", settings.max_pids),
        ("NanoCpus", settings.max_nano_cpus),
    )
    for key, maximum in checks:
        value = host.get(key)
        if not isinstance(value, int) or value <= 0 or value > maximum:
            problems.append(f"{key} must be between 1 and {maximum}")
    swap = host.get("MemorySwap")
    if swap not in (None, 0, host.get("Memory")):
        problems.append("swap must equal the memory limit")
    return problems


def _mount_problems(host: dict[str, Any], settings: ProxySettings) -> list[str]:
    problems = []
    for bind in host.get("Binds") or []:
        source = str(bind).split(":", 1)[0]
        if not _source_allowed(source, "bind" if source.startswith("/") else "volume", settings):
            problems.append(f"bind {source!r} is not allowed")
    for mount in host.get("Mounts") or []:
        kind = mount.get("Type")
        if kind == "tmpfs":
            continue
        source = str(mount.get("Source") or "")
        if kind not in ("bind", "volume") or not _source_allowed(source, kind, settings):
            problems.append(f"{kind} mount of {source!r} is not allowed")
            continue
        subpath = str((mount.get("VolumeOptions") or {}).get("Subpath") or "")
        if subpath and (subpath.startswith("/") or ".." in subpath.split("/")):
            problems.append(f"volume subpath {subpath!r} is not allowed")
    return problems


def _source_allowed(source: str, kind: str, settings: ProxySettings) -> bool:
    if kind == "volume":
        return source in settings.allowed_volumes
    path = posixpath.normpath(source)
    return any(
        path == root or path.startswith(root.rstrip("/") + "/")
        for root in settings.allowed_bind_roots
    )


def _list_is_filtered(query: str) -> bool:
    filters = parse_qs(query).get("filters", [])
    try:
        parsed = json.loads(filters[0]) if filters else {}
    except json.JSONDecodeError:
        return False
    labels = parsed.get("label") if isinstance(parsed, dict) else None
    if isinstance(labels, dict):  # {"devagent.managed=true": true} form
        labels = list(labels)
    return isinstance(labels, list) and f"{MANAGED_LABEL}=true" in labels


# ---------------------------------------------------------------------- HTTP plumbing
@dataclass
class Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((v for k, v in self.headers if k.lower() == lowered), None)

    @property
    def path(self) -> str:
        return _VERSION_PREFIX.sub("", unquote(urlsplit(self.target).path))

    @property
    def query(self) -> str:
        return urlsplit(self.target).query

    def encode(self, body: bytes, *, keep_alive_upgrade: bool) -> bytes:
        headers = [
            (k, v) for k, v in self.headers if k.lower() not in ("connection", "content-length")
        ]
        headers.append(("Connection", "Upgrade" if keep_alive_upgrade else "close"))
        if body or self.method in ("POST", "PUT"):
            headers.append(("Content-Length", str(len(body))))
        lines = [f"{self.method} {self.target} {self.version}"] + [f"{k}: {v}" for k, v in headers]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def parse_head(head: bytes) -> Request:
    text = head.decode("latin-1")
    request_line, *header_lines = text.split("\r\n")
    method, target, version = request_line.split(" ", 2)
    headers = []
    for line in header_lines:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers.append((name.strip(), value.strip()))
    return Request(method=method, target=target, version=version, headers=headers)


async def _read_request(
    head: bytes, reader: asyncio.StreamReader
) -> tuple[Request, bytes] | Denied:
    if len(head) > MAX_HEAD_BYTES:
        return Denied(400, "request head too large")
    try:
        request = parse_head(head[:-4])
        length = int(request.header("Content-Length") or 0)
    except ValueError:
        return Denied(400, "malformed request")
    if request.header("Transfer-Encoding"):
        return Denied(400, "chunked request bodies are not supported")
    if not 0 <= length <= MAX_BODY_BYTES:
        return Denied(400, "request body too large")
    body = await reader.readexactly(length) if length else b""
    return request, body


def _response(status: int, reason: str) -> bytes:
    body = json.dumps({"message": f"devagent docker proxy: {reason}"}).encode()
    phrase = {400: "Bad Request", 403: "Forbidden", 404: "Not Found", 502: "Bad Gateway"}
    head = (
        f"HTTP/1.1 {status} {phrase.get(status, 'Error')}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


class DockerProxy:
    def __init__(
        self,
        settings: ProxySettings,
        open_upstream: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._open_upstream = open_upstream or (
            lambda: asyncio.open_unix_connection(settings.upstream_socket)
        )

    async def serve(self) -> asyncio.Server:
        return await asyncio.start_server(
            self.handle, self._settings.listen_host, self._settings.listen_port
        )

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                if not await self._handle_one(reader, writer):
                    break
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass  # client went away mid-request
        finally:
            writer.close()
            with _suppress_connection_errors():
                await writer.wait_closed()

    async def _handle_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """Serve one request. Returns True if the client connection can take another."""
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            return False
        parsed = await _read_request(head, reader)
        if isinstance(parsed, Denied):
            writer.write(_response(parsed.status, parsed.reason))
            return False
        request, body = parsed

        denied = await self._authorize(request, body)
        log = logger.bind(method=request.method, path=request.path)
        if denied is not None:
            log.warning("docker_request_denied", reason=denied.reason)
            writer.write(_response(denied.status, denied.reason))
            await writer.drain()
            return True
        log.debug("docker_request_allowed")
        await self._forward(request, body, reader, writer)
        # The upstream response was delimited by closing the connection; the client
        # cannot know where it ended otherwise, so close ours too.
        return False

    async def _forward(
        self,
        request: Request,
        body: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        upgrade = (request.header("Upgrade") or "").lower() == "tcp"
        try:
            up_reader, up_writer = await self._open_upstream()
        except OSError as exc:
            writer.write(_response(502, f"docker daemon unavailable: {exc}"))
            return
        try:
            up_writer.write(request.encode(body, keep_alive_upgrade=upgrade))
            await up_writer.drain()
            if upgrade:
                await asyncio.gather(_pipe(up_reader, writer), _pipe(reader, up_writer))
            else:
                await _pipe(up_reader, writer)
        finally:
            up_writer.close()

    async def _authorize(self, request: Request, body: bytes) -> Denied | None:
        method, path = request.method, request.path
        if method in ("GET", "HEAD") and path in ("/_ping", "/version"):
            return None
        if method == "GET" and path == "/containers/json":
            ok = _list_is_filtered(request.query)
            return (
                None if ok else Denied(403, f"container lists must filter on {MANAGED_LABEL}=true")
            )
        if method == "POST" and path == "/containers/create":
            return self._authorize_create(body)
        if method == "GET" and path.startswith("/images/") and path.endswith("/json"):
            name = path.removeprefix("/images/").removesuffix("/json")
            ok = name in self._settings.allowed_images
            return None if ok else Denied(403, f"image {name!r} is not allowed")
        for route_method, pattern in _SCOPED_ROUTES:
            match = pattern.match(path)
            if match and method == route_method:
                ok = await self._is_managed(match.group("id"))
                return None if ok else Denied(403, "container is not a DevAgent sandbox")
        return Denied(403, f"{method} {path} is not allowed")

    def _authorize_create(self, body: bytes) -> Denied | None:
        try:
            parsed = json.loads(body or b"null")
        except json.JSONDecodeError:
            return Denied(400, "invalid JSON body")
        problems = validate_create(parsed, self._settings)
        return Denied(403, "; ".join(problems)) if problems else None

    async def _is_managed(self, container_id: str) -> bool:
        reader, writer = await self._open_upstream()
        try:
            writer.write(
                f"GET /containers/{container_id}/json HTTP/1.0\r\nHost: docker\r\n\r\n".encode()
            )
            await writer.drain()
            raw = await reader.read()
        finally:
            writer.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        if not head.startswith((b"HTTP/1.0 200", b"HTTP/1.1 200")):
            return False
        try:
            labels = (json.loads(payload).get("Config") or {}).get("Labels") or {}
        except (json.JSONDecodeError, AttributeError):
            return False
        return bool(labels.get(MANAGED_LABEL) == "true")


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(65536):
            sink.write(chunk)
            await sink.drain()
        if sink.can_write_eof():
            sink.write_eof()
    except (ConnectionError, OSError):
        pass


class _suppress_connection_errors:  # noqa: N801 - used like contextlib.suppress
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, (ConnectionError, OSError))


async def _main() -> None:
    settings = ProxySettings()
    import logging  # noqa: PLC0415 - only needed for the standalone process

    logging.basicConfig(level=settings.log_level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        )
    )
    server = await DockerProxy(settings).serve()
    logger.info(
        "docker_proxy_listening",
        port=settings.listen_port,
        images=settings.allowed_images,
        volumes=settings.allowed_volumes,
        networks=settings.allowed_networks,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
