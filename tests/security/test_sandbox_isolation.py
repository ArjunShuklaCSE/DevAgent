"""Sandbox isolation, checked against real containers (spec 6.3 and 16).

Each test runs a probe script inside the sandbox, the way an untrusted repository
would, and checks what it could and could not do.
"""

import asyncio
import time

import docker
import pytest

from sandbox.docker_sandbox import LABEL_MANAGED, CommandResult, DockerSandbox, RunWorkspace
from sandbox.policy import PolicyViolationError
from tests.sandbox_support import (
    docker_client,
    sandbox,
    sandbox_image_name,
    workspace,
    write_files,
)

__all__ = ["docker_client", "sandbox", "workspace"]  # fixtures

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def run_probe(
    sandbox: DockerSandbox, ws: RunWorkspace, code: str, timeout_seconds: float | None = None
) -> CommandResult:
    await write_files(sandbox, ws, {"probe.py": code})
    return await sandbox.run(ws, ["python", "probe.py"], timeout_seconds=timeout_seconds)


async def test_no_network(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    result = await run_probe(
        sandbox,
        workspace,
        "import socket\n"
        "for target in [('1.1.1.1', 53), ('pypi.org', 443)]:\n"
        "    try:\n"
        "        socket.create_connection(target, timeout=3)\n"
        "        print('CONNECTED', target)\n"
        "    except OSError as exc:\n"
        "        print('blocked', target, type(exc).__name__)\n"
        "print(sorted(n for _, n in socket.if_nameindex()))\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "CONNECTED" not in result.stdout
    assert result.stdout.count("blocked") == 2
    assert "['lo']" in result.stdout  # loopback is the only interface


async def test_worker_environment_does_not_leak(
    sandbox: DockerSandbox,
    workspace: RunWorkspace,
    docker_client: docker.DockerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVAGENT_TEST_CANARY", "leaked-secret-value")
    result = await run_probe(
        sandbox, workspace, "import os\nfor k, v in sorted(os.environ.items()): print(f'{k}={v}')\n"
    )
    assert result.exit_code == 0, result.stderr
    assert "leaked-secret-value" not in result.stdout
    names = {line.split("=", 1)[0] for line in result.stdout.splitlines()}
    image_env = docker_client.images.get(sandbox_image_name(sandbox)).attrs["Config"]["Env"]
    from_image = {item.split("=", 1)[0] for item in image_env}
    set_by_sandbox = {"VIRTUAL_ENV", "HOME", "PIP_NO_INPUT", "HOSTNAME"}
    assert names <= from_image | set_by_sandbox, names - from_image - set_by_sandbox
    assert "HTTPS_PROXY" not in names  # install-only settings never reach agent commands


async def test_unprivileged_and_read_only(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    result = await run_probe(
        sandbox,
        workspace,
        "import os, pathlib\n"
        "print('uid', os.getuid(), os.getgid())\n"
        "status = pathlib.Path('/proc/self/status').read_text()\n"
        "print([l for l in status.splitlines() if l.startswith(('CapEff', 'NoNewPrivs'))])\n"
        "paths = ['/usr/local/lib/evil', '/etc/evil', '/env/evil', '/workspace/ok', '/tmp/ok']\n"
        "for path in paths:\n"
        "    try:\n"
        "        pathlib.Path(path).write_text('x')\n"
        "        print('wrote', path)\n"
        "    except OSError as exc:\n"
        "        print('denied', path, exc.errno)\n"
        "print('docker.sock', os.path.exists('/var/run/docker.sock'))\n",
    )
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    uid, gid = sandbox.config.user.split(":")
    assert f"uid {uid} {gid}" in out
    assert uid != "0"
    assert "'CapEff:\\t0000000000000000'" in out
    assert "'NoNewPrivs:\\t1'" in out
    for path in ("/usr/local/lib/evil", "/etc/evil", "/env/evil"):
        assert f"denied {path}" in out
    assert "wrote /workspace/ok" in out
    assert "wrote /tmp/ok" in out
    assert "docker.sock False" in out


async def test_git_metadata_is_hidden_from_the_sandbox(
    sandbox: DockerSandbox, workspace: RunWorkspace
) -> None:
    git_dir = workspace.repo / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n\tbare = false\n")
    result = await run_probe(
        sandbox,
        workspace,
        "import os, pathlib\n"
        "print('listing', sorted(os.listdir('/workspace/.git')))\n"
        "try:\n"
        "    config = pathlib.Path('/workspace/.git/config')\n"
        "    config.write_text('[filter \"x\"]\\n\\tclean = id\\n')\n"
        "    print('wrote config')\n"
        "except OSError as exc:\n"
        "    print('denied', exc.errno)\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "listing []" in result.stdout
    assert "denied" in result.stdout
    assert (git_dir / "config").read_text() == "[core]\n\tbare = false\n"


async def test_timeout_kills_the_command(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    started = time.monotonic()
    result = await run_probe(
        sandbox,
        workspace,
        "import time\nprint('start', flush=True)\ntime.sleep(60)\n",
        timeout_seconds=2,
    )
    assert result.timed_out
    assert not result.ok
    assert "start" in result.stdout
    assert time.monotonic() - started < 20


async def test_memory_limit(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    result = await run_probe(
        sandbox,
        workspace,
        "blocks = []\nwhile True:\n    blocks.append(bytearray(32 * 1024 * 1024))\n",
    )
    assert result.exit_code != 0
    assert result.oom_killed or "MemoryError" in result.stderr


async def test_pid_limit(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    result = await run_probe(
        sandbox,
        workspace,
        "import threading, time\n"
        "started = 0\n"
        "try:\n"
        "    for _ in range(500):\n"
        "        threading.Thread(target=time.sleep, args=(5,), daemon=True).start()\n"
        "        started += 1\n"
        "except RuntimeError as exc:\n"
        "    print('limited', started, exc)\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "limited" in result.stdout
    assert int(result.stdout.split()[1]) < 64


async def test_output_is_capped(sandbox: DockerSandbox, workspace: RunWorkspace) -> None:
    result = await run_probe(
        sandbox, workspace, "import sys\nsys.stdout.write('A' * 1_000_000 + 'THE-END')\n"
    )
    assert result.exit_code == 0
    assert result.stdout_truncated
    assert len(result.stdout) < 70_000
    assert result.stdout.endswith("THE-END")
    assert "bytes of output omitted" in result.stdout


async def test_policy_rejects_before_any_container_starts(
    sandbox: DockerSandbox, workspace: RunWorkspace, docker_client: docker.DockerClient
) -> None:
    await sandbox.prepare(workspace)
    for argv in (["bash", "-c", "id"], ["python", "-c", "print(1)"], ["cat", "/etc/shadow"]):
        with pytest.raises(PolicyViolationError):
            await sandbox.run(workspace, argv)
    filters: dict[str, str | list[str] | bool] = {
        "label": [f"{LABEL_MANAGED}=true", f"devagent.run_id={workspace.run_id}"]
    }
    assert docker_client.containers.list(all=True, filters=filters) == []


async def test_cancellation_removes_the_container(
    sandbox: DockerSandbox, workspace: RunWorkspace, docker_client: docker.DockerClient
) -> None:
    await write_files(sandbox, workspace, {"probe.py": "import time\ntime.sleep(60)\n"})
    task = asyncio.create_task(sandbox.run(workspace, ["python", "probe.py"]))
    filters: dict[str, str | list[str] | bool] = {"label": [f"devagent.run_id={workspace.run_id}"]}
    for _ in range(50):
        if docker_client.containers.list(filters=filters):
            break
        await asyncio.sleep(0.2)
    assert docker_client.containers.list(filters=filters), "container never started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert docker_client.containers.list(all=True, filters=filters) == []


async def test_kill_run_and_reaper(
    sandbox: DockerSandbox, workspace: RunWorkspace, docker_client: docker.DockerClient
) -> None:
    await write_files(sandbox, workspace, {"probe.py": "import time\ntime.sleep(60)\n"})
    task = asyncio.create_task(sandbox.run(workspace, ["python", "probe.py"]))
    filters: dict[str, str | list[str] | bool] = {"label": [f"devagent.run_id={workspace.run_id}"]}
    for _ in range(50):
        if docker_client.containers.list(filters=filters):
            break
        await asyncio.sleep(0.2)
    assert await sandbox.reap(max_age_seconds=3600) == 0  # too young to reap
    assert await sandbox.kill_run(workspace.run_id) == 1
    result = await task
    assert not result.ok
    assert docker_client.containers.list(all=True, filters=filters) == []


async def test_install_then_tests_with_junit(
    sandbox: DockerSandbox, workspace: RunWorkspace
) -> None:
    await write_files(
        sandbox,
        workspace,
        {
            "requirements.txt": "",
            "calc.py": "def add(a, b):\n    return a - b\n",
            "test_calc.py": "from calc import add\n\n"
            "def test_add():\n    assert add(2, 2) == 4\n\n"
            "def test_zero():\n    assert add(0, 0) == 0\n",
        },
    )
    install = await sandbox.install(
        workspace, [["python", "-m", "pip", "install", "-r", "requirements.txt"]]
    )
    assert [r.exit_code for r in install] == [0, 0], [r.stderr for r in install]
    run = await sandbox.run_tests(workspace, ["python", "-m", "pytest"])
    assert run.result.exit_code == 1
    assert run.report is not None
    assert (run.report.total, run.report.passed, run.report.failed) == (2, 1, 1)
    assert [c.test_id for c in run.report.failing()] == ["test_calc::test_add"]
    assert not any(workspace.reports.iterdir())  # report files are consumed
    assert not (workspace.repo / ".pytest_cache").exists()
