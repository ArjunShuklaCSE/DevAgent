"""Host-side git helpers for the workspace: file listing and the diff against base.

All git processes use the hardened invocation from ``workspace.clone.run_git`` (no
hooks, no host config, no external diff or textconv), so reading the repository never
executes repository code.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from tools.errors import ToolError
from workspace.clone import CloneError, run_git

# Files the toolchain creates in the workspace that are never part of a change.
WORKSPACE_EXCLUDES = (
    "*.egg-info/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "junit*.xml",
)
_MARKER = "# devagent: toolchain artifacts"


async def git(root: Path, args: list[str], timeout_seconds: float = 60.0) -> str:
    """Run git in the workspace.

    ``safe.directory`` names exactly this workspace: when the worker runs as root in
    development, the tree belongs to the sandbox user. This is safe only because the
    sandbox never sees the real ``.git`` (it is masked, see ``sandbox.docker_sandbox``),
    so repository code cannot plant config such as filter drivers that git on the
    worker would execute.
    """
    home = Path(tempfile.mkdtemp(prefix="devagent-githome-"))
    try:
        return await run_git(
            ["-c", f"safe.directory={root}", *args], root, home, timeout_seconds=timeout_seconds
        )
    except CloneError as exc:
        raise ToolError("git_failed", str(exc)) from exc
    finally:
        await asyncio.to_thread(_rmtree, home)


def ensure_workspace_excludes(root: Path) -> None:
    """Add toolchain artifacts to ``.git/info/exclude`` (idempotent)."""
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if _MARKER in current:
        return
    block = "\n".join((_MARKER, *WORKSPACE_EXCLUDES))
    exclude.write_text(f"{current.rstrip()}\n{block}\n".lstrip(), encoding="utf-8")


async def list_files(root: Path) -> list[str]:
    """Tracked and untracked files, honouring ``.gitignore`` (spec 5, list_tree)."""
    await asyncio.to_thread(ensure_workspace_excludes, root)
    out = await git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    return sorted({p for p in out.split("\x00") if p and (root / p).is_file()})


async def diff_against_base(root: Path, base_commit: str) -> tuple[str, str]:
    """Return (unified diff, --stat) of the working tree against ``base_commit``.

    Untracked files are included; the index is only a scratch area in the workspace.
    """
    await asyncio.to_thread(ensure_workspace_excludes, root)
    await git(root, ["add", "--all"])
    common = ["diff", "--cached", "--no-color", "--no-ext-diff", "--no-textconv", "-M"]
    diff = await git(root, [*common, "--binary", base_commit])
    stat = await git(root, [*common, "--stat=120", base_commit])
    return diff, stat


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
