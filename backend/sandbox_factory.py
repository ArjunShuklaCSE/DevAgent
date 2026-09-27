"""Build the Docker sandbox from settings (the only place that knows both)."""

from pathlib import Path

import docker

from backend.config import Settings
from sandbox.docker_sandbox import DockerSandbox, SandboxConfig, SandboxLimits
from sandbox.policy import CommandPolicy

MIB = 1024 * 1024


def sandbox_config(settings: Settings) -> SandboxConfig:
    install_env: dict[str, str] = {}
    if settings.sandbox_install_proxy:  # empty string (Compose default) means none
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            install_env[key] = settings.sandbox_install_proxy
    return SandboxConfig(
        workspace_root=Path(settings.workspace_root),
        image=settings.sandbox_image,
        user=settings.sandbox_user,
        limits=SandboxLimits(
            cpus=settings.sandbox_cpus,
            memory_bytes=settings.sandbox_memory_mb * MIB,
            pids=settings.sandbox_pids,
            tmpfs_bytes=settings.sandbox_tmpfs_mb * MIB,
        ),
        workspace_volume=settings.sandbox_workspace_volume,
        install_network=settings.sandbox_install_network,
        install_env=install_env,
        extra_ca_file=Path(settings.sandbox_extra_ca_file)
        if settings.sandbox_extra_ca_file
        else None,
        runtime=settings.sandbox_runtime or None,
    )


def docker_client(settings: Settings) -> docker.DockerClient:
    if settings.docker_host:
        return docker.DockerClient(base_url=settings.docker_host, timeout=120)
    return docker.from_env(timeout=120)


def build_sandbox(settings: Settings, client: docker.DockerClient | None = None) -> DockerSandbox:
    return DockerSandbox(
        client or docker_client(settings),
        sandbox_config(settings),
        CommandPolicy.load(Path(settings.command_policy_path)),
    )
