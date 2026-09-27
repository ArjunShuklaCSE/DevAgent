"""The filtering Docker proxy against a real daemon (ADR 0007).

The sandbox runner works through the proxy unchanged, and everything a compromised
worker could use to escape (privileged or host-mounting containers, exec, other
containers, networks, volumes, image pulls and builds) is refused.
"""

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

import docker
import pytest
from docker.errors import APIError

from sandbox.docker_proxy import DockerProxy, ProxySettings
from sandbox.docker_sandbox import DockerSandbox, RunWorkspace
from tests.sandbox_support import docker_client, sandbox, workspace, write_files

__all__ = ["docker_client", "sandbox", "workspace"]  # fixtures

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture
async def proxied_client(
    sandbox: DockerSandbox, tmp_path: Path
) -> AsyncIterator[docker.DockerClient]:
    settings = ProxySettings(
        listen_host="127.0.0.1",
        listen_port=0,
        allowed_images=[sandbox.config.image],
        allowed_bind_roots=[str(tmp_path)],
        allowed_networks=["none", sandbox.config.install_network],
    )
    server = await DockerProxy(settings).serve()
    port = server.sockets[0].getsockname()[1]
    client = await asyncio.to_thread(docker.DockerClient, base_url=f"tcp://127.0.0.1:{port}")
    yield client
    client.close()
    server.close()
    await server.wait_closed()


@pytest.fixture
def proxied_sandbox(sandbox: DockerSandbox, proxied_client: docker.DockerClient) -> DockerSandbox:
    return DockerSandbox(proxied_client, sandbox.config, sandbox.policy)


async def test_sandbox_works_through_the_proxy(
    proxied_sandbox: DockerSandbox, workspace: RunWorkspace
) -> None:
    assert (await proxied_sandbox.image_id()).startswith("sha256:")
    await write_files(proxied_sandbox, workspace, {"probe.py": "print('hello from sandbox')\n"})
    result = await proxied_sandbox.run(workspace, ["python", "probe.py"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "hello from sandbox"
    timed_out = await proxied_sandbox.run(
        workspace, ["python", "-m", "pytest", "--version"], timeout_seconds=30
    )
    assert timed_out.ok
    assert await proxied_sandbox.reap(max_age_seconds=3600) == 0


def _create(client: docker.DockerClient, sandbox: DockerSandbox, **overrides: object) -> None:
    host_config: dict[str, Any] = {
        "network_mode": "none",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": 256 * 1024 * 1024,
        "memswap_limit": 256 * 1024 * 1024,
        "nano_cpus": 1_000_000_000,
        "pids_limit": 64,
    }
    create: dict[str, Any] = {"image": sandbox.config.image, "user": "10001:10001"}
    for key, value in overrides.items():
        (host_config if key in HOST_KEYS else create)[key] = value
    client.api.create_container(
        **create,
        command=["python", "--version"],
        labels={"devagent.managed": "true", "devagent.run_id": "proxy-test"},
        host_config=client.api.create_host_config(**host_config),
    )


HOST_KEYS = {
    "privileged",
    "binds",
    "network_mode",
    "pid_mode",
    "cap_add",
    "read_only",
    "security_opt",
    "devices",
}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"privileged": True}, "privileged"),
        ({"binds": ["/:/host:ro"]}, "bind"),
        ({"binds": ["/var/run/docker.sock:/var/run/docker.sock"]}, "bind"),
        ({"network_mode": "container:devagent-postgres-1"}, "network"),
        ({"pid_mode": "host"}, "PidMode"),
        ({"cap_add": ["SYS_ADMIN"]}, "capabilities"),
        ({"read_only": False}, "read-only"),
        ({"security_opt": ["no-new-privileges:true", "seccomp=unconfined"]}, "security"),
        ({"devices": ["/dev/kmsg:/dev/kmsg"]}, "Devices"),
        ({"user": "0"}, "non-root"),
        ({"image": "hello-world"}, "image"),
    ],
)
async def test_unsafe_container_is_refused(
    proxied_client: docker.DockerClient,
    sandbox: DockerSandbox,
    overrides: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(APIError) as info:
        await asyncio.to_thread(_create, proxied_client, sandbox, **overrides)
    assert info.value.status_code == 403
    assert reason in str(info.value.explanation)


async def test_other_endpoints_are_refused(proxied_client: docker.DockerClient) -> None:
    api = proxied_client.api
    calls: dict[str, Any] = {
        "networks": api.networks,
        "volumes": api.volumes,
        "pull": partial(api.pull, "alpine", tag="latest"),
        "unfiltered list": partial(api.containers, all=True),
        "info": api.info,
        "images list": api.images,
        "other image": partial(api.inspect_image, "postgres:16-alpine"),
    }
    for name, call in calls.items():
        with pytest.raises(APIError) as info:
            await asyncio.to_thread(call)
        assert info.value.status_code == 403, name


async def test_containers_not_created_by_devagent_are_off_limits(
    proxied_client: docker.DockerClient, docker_client: docker.DockerClient
) -> None:
    outsider = await asyncio.to_thread(
        docker_client.containers.create, "hello-world", labels={"app": "not-devagent"}
    )
    outsider_id = str(outsider.id)
    api = proxied_client.api
    calls: list[Any] = [
        partial(api.inspect_container, outsider_id),
        partial(api.start, outsider_id),
        partial(api.kill, outsider_id),
        partial(api.remove_container, outsider_id, force=True),
        partial(api.logs, outsider_id),
    ]
    try:
        for call in calls:
            with pytest.raises(APIError) as info:
                await asyncio.to_thread(call)
            assert info.value.status_code == 403
        outsider.reload()  # still there, untouched
        assert outsider.status == "created"
    finally:
        await asyncio.to_thread(outsider.remove, force=True)


async def test_exec_is_refused_even_on_sandbox_containers(
    proxied_client: docker.DockerClient,
    proxied_sandbox: DockerSandbox,
    workspace: RunWorkspace,
    docker_client: docker.DockerClient,
) -> None:
    await write_files(proxied_sandbox, workspace, {"probe.py": "import time\ntime.sleep(30)\n"})
    task = asyncio.create_task(proxied_sandbox.run(workspace, ["python", "probe.py"]))
    filters: dict[str, str | list[str] | bool] = {"label": [f"devagent.run_id={workspace.run_id}"]}
    running = []
    for _ in range(50):
        running = await asyncio.to_thread(docker_client.containers.list, filters=filters)
        if running:
            break
        await asyncio.sleep(0.2)
    assert running
    try:
        with pytest.raises(APIError) as info:
            await asyncio.to_thread(proxied_client.api.exec_create, running[0].id, ["id"])
        assert info.value.status_code == 403
    finally:
        assert await proxied_sandbox.kill_run(workspace.run_id) == 1
        await task


async def test_malformed_requests(sandbox: DockerSandbox, tmp_path: Path) -> None:
    settings = ProxySettings(listen_host="127.0.0.1", listen_port=0)
    server = await DockerProxy(settings).serve()
    port = server.sockets[0].getsockname()[1]
    try:
        for raw in (
            b"POST /containers/create HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            b"POST /containers/create HTTP/1.1\r\nContent-Length: 99999999\r\n\r\n",
            b"POST /containers/create HTTP/1.1\r\nContent-Length: 5\r\n\r\n{nope",
            b"garbage\r\n\r\n",
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(raw)
            await writer.drain()
            status = await reader.readline()
            writer.close()
            assert status.startswith(b"HTTP/1.1 400"), (raw, status)
    finally:
        server.close()
        await server.wait_closed()
