"""Worker jobs that manage the sandbox: self-check and orphan reaping."""

import asyncio
import shutil
import uuid
from typing import Any

import structlog

from backend.config import get_settings
from sandbox.docker_sandbox import DockerSandbox, RunWorkspace, SandboxError

logger = structlog.get_logger(__name__)


def _sandbox(ctx: dict[str, Any]) -> DockerSandbox:
    sandbox: DockerSandbox | None = ctx.get("sandbox")
    if sandbox is None or ctx.get("sandbox_image_id") is None:
        raise SandboxError("sandbox_unavailable", "Docker or the sandbox image is not available")
    return sandbox


_CHECK_SCRIPT = """\
import os, socket, pathlib
print("uid", os.getuid())
print("interfaces", ",".join(sorted(n for _, n in socket.if_nameindex())))
try:
    pathlib.Path("/usr/devagent-check").write_text("x")
    print("root_fs", "writable")
except OSError:
    print("root_fs", "read-only")
"""


async def sandbox_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Run a probe in a real sandbox through the worker's Docker path.

    Proves the deployment wiring end to end: proxy, image, shared workspace volume,
    non-root user, no network, read-only root filesystem.
    """
    sandbox = _sandbox(ctx)
    root = sandbox.config.workspace_root
    workspace = RunWorkspace.under(root, f"selfcheck-{uuid.uuid4().hex[:12]}")
    try:
        await asyncio.to_thread(workspace.repo.mkdir, parents=True)
        await asyncio.to_thread((workspace.repo / "check.py").write_text, _CHECK_SCRIPT)
        await sandbox.prepare(workspace)
        result = await sandbox.run(workspace, ["python", "check.py"], timeout_seconds=60)
    finally:
        await asyncio.to_thread(shutil.rmtree, workspace.root, True)
    facts = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    return {
        "exit_code": result.exit_code,
        "image_id": ctx.get("sandbox_image_id"),
        "facts": facts,
        "stderr": result.stderr[-2000:],
    }


async def reap_sandboxes(ctx: dict[str, Any]) -> int:
    """Remove sandbox containers left behind by crashed workers."""
    sandbox = _sandbox(ctx)
    return await sandbox.reap(get_settings().sandbox_reap_after_seconds)
