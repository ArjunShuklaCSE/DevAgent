"""Run commands in throwaway Docker containers (spec 6.3).

Every command gets a fresh container from the pinned sandbox image, with:
- a non-root user, all capabilities dropped, ``no-new-privileges``, a read-only root
  filesystem and a size-limited ``/tmp`` tmpfs;
- CPU, memory (swap disabled) and PID limits, plus an optional runtime such as gVisor;
- no network, except for the dependency-install profile;
- an explicit environment: nothing from the worker's environment leaks in;
- the run's workspace mounted at ``/workspace``, its virtualenv at ``/env`` (read-only
  outside of install) and a report directory at ``/reports``;
- a wall-clock timeout and a bound on captured output.

One container per command (rather than ``docker exec`` into a long-lived container)
keeps each command's limits and exit status independent, lets the Docker proxy deny
``exec`` entirely, and means a timed-out or cancelled command is cleaned up by removing
one container. Containers carry ``devagent.*`` labels so ``kill_run`` and ``reap`` can
find them after a crash.
"""

import asyncio
import contextlib
import os
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import structlog
from docker import DockerClient
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.types import LogConfig, Mount

from sandbox.junit import JUnitError, TestReport, parse_junit_file
from sandbox.output import BoundedBuffer
from sandbox.policy import CommandLimits, CommandPolicy, Profile

logger = structlog.get_logger(__name__)

LABEL_MANAGED: Final = "devagent.managed"
LABEL_RUN: Final = "devagent.run_id"
LABEL_PROFILE: Final = "devagent.profile"
LABEL_CREATED: Final = "devagent.created"

CONTAINER_WORKSPACE: Final = "/workspace"
CONTAINER_ENV: Final = "/env"
CONTAINER_REPORTS: Final = "/reports"
CONTAINER_CA: Final = "/env/.devagent-ca.pem"

# Output beyond this is not even streamed: the command is killed (log flooding).
HARD_OUTPUT_LIMIT_BYTES: Final = 64 * 1024 * 1024
_EXIT_WAIT_SECONDS: Final = 30.0


class SandboxError(Exception):
    """The sandbox itself failed (Docker unavailable, image missing, API error)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 1.0
    memory_bytes: int = 1024 * 1024 * 1024
    pids: int = 256
    tmpfs_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True)
class SandboxConfig:
    workspace_root: Path
    image: str = "devagent-sandbox:dev"
    user: str = "10001:10001"
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    # Named volume that holds ``workspace_root`` (Compose). ``None`` means the worker
    # and the Docker daemon share a filesystem and run directories are bind-mounted.
    workspace_volume: str | None = None
    install_network: str = "bridge"
    install_env: Mapping[str, str] = field(default_factory=dict)
    extra_ca_file: Path | None = None
    runtime: str | None = None  # e.g. "runsc" for gVisor


@dataclass(frozen=True)
class RunWorkspace:
    """The per-run directory layout on the worker's filesystem."""

    run_id: str
    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def env(self) -> Path:
        return self.root / "env"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @classmethod
    def under(cls, workspace_root: Path, run_id: str) -> "RunWorkspace":
        return cls(run_id=run_id, root=workspace_root / run_id)


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    profile: Profile
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float
    timed_out: bool = False
    oom_killed: bool = False
    output_limit_exceeded: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest test class

    result: CommandResult
    report: TestReport | None
    report_error: str | None = None


class DockerSandbox:
    def __init__(self, client: DockerClient, config: SandboxConfig, policy: CommandPolicy) -> None:
        self._client = client
        self._config = config
        self._policy = policy

    @property
    def policy(self) -> CommandPolicy:
        return self._policy

    # ------------------------------------------------------------------ setup
    async def image_id(self) -> str:
        """Return the local image ID of the sandbox image (recorded with every run)."""

        def inspect() -> str:
            try:
                image = self._client.images.get(self._config.image)
            except ImageNotFound as exc:
                raise SandboxError(
                    "sandbox_image_missing",
                    f"sandbox image {self._config.image} not found; build docker/sandbox.Dockerfile",
                ) from exc
            return str(image.id)

        return await asyncio.to_thread(inspect)

    async def prepare(self, workspace: RunWorkspace) -> None:
        """Create the env and report directories next to an existing ``repo`` checkout."""
        await asyncio.to_thread(self._prepare_sync, workspace)

    def _prepare_sync(self, workspace: RunWorkspace) -> None:
        workspace.env.mkdir(parents=True, exist_ok=True)
        workspace.reports.mkdir(parents=True, exist_ok=True)
        if self._config.extra_ca_file is not None:
            shutil.copyfile(self._config.extra_ca_file, workspace.env / Path(CONTAINER_CA).name)
        if os.geteuid() == 0:
            # A root worker (local development) must hand the tree to the sandbox user.
            uid, gid = (int(part) for part in self._config.user.split(":"))
            for path in (workspace.root, *workspace.root.rglob("*")):
                os.lchown(path, uid, gid)

    # ------------------------------------------------------------------ commands
    async def run(
        self,
        workspace: RunWorkspace,
        argv: Sequence[str],
        *,
        profile: Profile = "run",
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Run one command. Raises ``PolicyViolationError`` before touching Docker."""
        limits = self._policy.check(argv, profile, timeout_seconds)
        return await self._execute(workspace, tuple(argv), profile, limits)

    async def install(
        self, workspace: RunWorkspace, commands: Sequence[Sequence[str]]
    ) -> list[CommandResult]:
        """Create the run's virtualenv and run the install commands; stop at the first failure."""
        results = [
            await self.run(
                workspace,
                ["python", "-m", "venv", "--system-site-packages", CONTAINER_ENV],
                profile="install",
            )
        ]
        for command in commands:
            if not results[-1].ok:
                break
            results.append(await self.run(workspace, command, profile="install"))
        return results

    async def run_tests(
        self,
        workspace: RunWorkspace,
        test_command: Sequence[str],
        extra_args: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> TestRun:
        """Run the test command; for pytest, also collect a JUnit report."""
        argv = [*test_command, *extra_args]
        report_name = f"junit-{uuid.uuid4().hex}.xml"
        is_pytest = argv[0] == "pytest" or argv[:3] in (
            ["python", "-m", "pytest"],
            ["python3", "-m", "pytest"],
        )
        if is_pytest:
            argv += [f"--junitxml={CONTAINER_REPORTS}/{report_name}", "-p", "no:cacheprovider"]
        result = await self.run(workspace, argv, timeout_seconds=timeout_seconds)
        if not is_pytest:
            return TestRun(result=result, report=None, report_error="not a pytest command")
        path = workspace.reports / report_name
        try:
            report = await asyncio.to_thread(parse_junit_file, path)
        except JUnitError as exc:
            return TestRun(result=result, report=None, report_error=str(exc))
        finally:
            path.unlink(missing_ok=True)
        return TestRun(result=result, report=report)

    async def _execute(
        self, workspace: RunWorkspace, argv: tuple[str, ...], profile: Profile, limits: CommandLimits
    ) -> CommandResult:
        started = time.monotonic()
        stdout = BoundedBuffer(limits.max_output_bytes)
        stderr = BoundedBuffer(limits.max_output_bytes)
        container, stream = await asyncio.to_thread(
            self._create_and_start, workspace, argv, profile, limits
        )
        log = logger.bind(run_id=workspace.run_id, container=container.short_id, argv=list(argv))
        timed_out = False
        flooded = False
        try:
            reader = asyncio.ensure_future(
                asyncio.to_thread(_pump, stream, stdout, stderr, container)
            )
            done, _ = await asyncio.wait({reader}, timeout=limits.timeout_seconds)
            if not done:
                timed_out = True
                await asyncio.to_thread(_kill_quietly, container)
            flooded = await asyncio.wait_for(reader, timeout=_EXIT_WAIT_SECONDS)
            exit_code, oom = await asyncio.to_thread(_exit_status, container)
        finally:
            # Also runs on cancellation: removing the container stops the command.
            await asyncio.to_thread(_remove_quietly, container)

        result = CommandResult(
            argv=argv,
            profile=profile,
            exit_code=exit_code,
            stdout=stdout.text(),
            stderr=stderr.text(),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            duration_seconds=round(time.monotonic() - started, 3),
            timed_out=timed_out,
            oom_killed=oom,
            output_limit_exceeded=flooded,
        )
        log.info(
            "sandbox_command_finished",
            profile=profile,
            exit_code=exit_code,
            timed_out=timed_out,
            oom_killed=oom,
            duration_seconds=result.duration_seconds,
        )
        return result

    def _create_and_start(
        self, workspace: RunWorkspace, argv: tuple[str, ...], profile: Profile, limits: CommandLimits
    ) -> tuple[Container, Any]:
        cfg = self._config
        try:
            container = self._client.containers.create(
                image=cfg.image,
                command=list(argv),
                user=cfg.user,
                working_dir=CONTAINER_WORKSPACE,
                environment=self._environment(profile),
                hostname="sandbox",
                network_mode=cfg.install_network if limits.network else "none",
                read_only=True,
                tmpfs={"/tmp": f"rw,nosuid,nodev,size={cfg.limits.tmpfs_bytes}"},  # noqa: S108
                mounts=self._mounts(workspace, profile),
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=cfg.limits.memory_bytes,
                memswap_limit=cfg.limits.memory_bytes,
                nano_cpus=int(cfg.limits.cpus * 1e9),
                pids_limit=cfg.limits.pids,
                init=True,
                runtime=cfg.runtime,
                log_config=LogConfig(type="json-file", config={"max-size": "1m", "max-file": "1"}),
                labels={
                    LABEL_MANAGED: "true",
                    LABEL_RUN: workspace.run_id,
                    LABEL_PROFILE: profile,
                    LABEL_CREATED: str(int(time.time())),
                },
                detach=True,
            )
            # Attach before start so no output is lost, then start.
            stream = self._client.api.attach(
                container.id, stdout=True, stderr=True, stream=True, demux=True
            )
            container.start()
        except (APIError, ImageNotFound) as exc:
            raise SandboxError("sandbox_start_failed", f"could not start sandbox: {exc}") from exc
        return container, stream

    def _environment(self, profile: Profile) -> dict[str, str]:
        env = {
            "PATH": f"{CONTAINER_ENV}/bin:/usr/local/bin:/usr/bin:/bin",
            "VIRTUAL_ENV": CONTAINER_ENV,
            "HOME": "/tmp",  # noqa: S108 - tmpfs inside the container
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
        if profile == "install":
            env.update(self._config.install_env)
            if self._config.extra_ca_file is not None:
                env["PIP_CERT"] = CONTAINER_CA
                env["SSL_CERT_FILE"] = CONTAINER_CA
        return env

    def _mounts(self, workspace: RunWorkspace, profile: Profile) -> list[Mount]:
        writable_env = profile == "install"
        return [
            self._mount(workspace.repo, CONTAINER_WORKSPACE, read_only=False),
            self._mount(workspace.env, CONTAINER_ENV, read_only=not writable_env),
            self._mount(workspace.reports, CONTAINER_REPORTS, read_only=False),
        ]

    def _mount(self, path: Path, target: str, *, read_only: bool) -> Mount:
        volume = self._config.workspace_volume
        if volume is None:
            return Mount(target=target, source=str(path), type="bind", read_only=read_only)
        subpath = path.relative_to(self._config.workspace_root).as_posix()
        return Mount(
            target=target,
            source=volume,
            type="volume",
            read_only=read_only,
            no_copy=True,
            subpath=subpath,
        )

    # ------------------------------------------------------------------ cleanup
    async def kill_run(self, run_id: str) -> int:
        """Remove every container of a run (used on cancel). Returns how many."""
        return await asyncio.to_thread(self._remove_matching, {LABEL_RUN: run_id}, None)

    async def reap(self, max_age_seconds: float) -> int:
        """Remove sandbox containers older than ``max_age_seconds`` (crash leftovers)."""
        cutoff = time.time() - max_age_seconds
        return await asyncio.to_thread(self._remove_matching, {}, cutoff)

    def _remove_matching(self, labels: dict[str, str], created_before: float | None) -> int:
        filters = [f"{LABEL_MANAGED}=true", *(f"{k}={v}" for k, v in labels.items())]
        containers = self._client.containers.list(all=True, filters={"label": filters})
        removed = 0
        for container in containers:
            created = float(container.labels.get(LABEL_CREATED, "0") or 0)
            if created_before is not None and created >= created_before:
                continue
            _remove_quietly(container)
            removed += 1
        if removed:
            logger.info("sandbox_containers_removed", count=removed, labels=labels)
        return removed


def _pump(stream: Any, stdout: BoundedBuffer, stderr: BoundedBuffer, container: Container) -> bool:
    """Copy the attach stream into the buffers; kill the container on output flooding."""
    for out, err in stream:
        if out:
            stdout.write(out)
        if err:
            stderr.write(err)
        if stdout.total_bytes + stderr.total_bytes > HARD_OUTPUT_LIMIT_BYTES:
            _kill_quietly(container)
            return True
    return False


def _exit_status(container: Container) -> tuple[int | None, bool]:
    status = container.wait(timeout=_EXIT_WAIT_SECONDS)
    container.reload()
    oom = bool(container.attrs.get("State", {}).get("OOMKilled", False))
    code = status.get("StatusCode")
    return (int(code) if code is not None else None), oom


def _kill_quietly(container: Container) -> None:
    with contextlib.suppress(NotFound, APIError):  # already exited or removed
        container.kill()


def _remove_quietly(container: Container) -> None:
    try:
        container.remove(force=True)
    except NotFound:
        pass
    except APIError as exc:
        # 409 "removal already in progress" is fine; anything else is logged, not raised,
        # because this runs in cleanup paths and the reaper retries later.
        logger.warning("sandbox_remove_failed", container=container.short_id, error=str(exc))
