"""Fixtures for tests that start real sandbox containers (need Docker and the image)."""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path

import docker
import pytest

from backend.config import Settings
from backend.sandbox_factory import sandbox_config
from sandbox.docker_sandbox import DockerSandbox, RunWorkspace, SandboxLimits
from sandbox.policy import CommandPolicy

POLICY_PATH = Path(__file__).parents[1] / "config" / "command_policy.yaml"
TEST_LIMITS = SandboxLimits(
    cpus=1.0, memory_bytes=256 * 1024 * 1024, pids=64, tmpfs_bytes=64 * 1024 * 1024
)


@pytest.fixture
def docker_client() -> Iterator[docker.DockerClient]:
    client = docker.from_env(timeout=120)
    yield client
    client.close()


@pytest.fixture
def sandbox(docker_client: docker.DockerClient, tmp_path: Path) -> DockerSandbox:
    # A root test process hands run directories to the default sandbox user; a non-root
    # one (CI runners) runs the sandbox as itself, which is still unprivileged.
    user = "10001:10001" if os.geteuid() == 0 else f"{os.getuid()}:{os.getgid()}"
    config = replace(
        sandbox_config(Settings()),
        workspace_root=tmp_path,
        limits=TEST_LIMITS,
        workspace_volume=None,
        user=user,
    )
    return DockerSandbox(docker_client, config, CommandPolicy.load(POLICY_PATH))


@pytest.fixture
async def workspace(sandbox: DockerSandbox, tmp_path: Path) -> AsyncIterator[RunWorkspace]:
    ws = RunWorkspace.under(tmp_path, f"test-{uuid.uuid4().hex[:12]}")
    ws.repo.mkdir(parents=True)
    yield ws
    await sandbox.kill_run(ws.run_id)


async def write_files(sandbox: DockerSandbox, ws: RunWorkspace, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = ws.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    await sandbox.prepare(ws)


def sandbox_image_name(sandbox: DockerSandbox) -> str:
    return sandbox.config.image
