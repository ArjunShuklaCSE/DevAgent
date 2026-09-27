"""Safe repository cloning (spec 6.4).

Cloning must never execute repository code. Every git process runs with:
- an argv list (no shell), a scrubbed environment, ``HOME`` in a temp dir, and no
  system/global git config, so host config cannot add hooks, helpers or filters;
- ``core.hooksPath=/dev/null`` and an empty template dir: no hooks run;
- ``core.symlinks=false``: symlinks are checked out as plain files containing the
  link target, so nothing in the workspace can point outside it;
- no submodule recursion, Git LFS smudge disabled, ``file://``/``ext::`` protocols off;
- a shallow fetch of a single ref, a wall-clock timeout, and size/file-count limits
  enforced while the fetch is running and again after checkout.
Credentials, when given, are passed via ``GIT_CONFIG_*`` environment variables for the
single fetch and are never written to ``.git/config``.
"""

import asyncio
import base64
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import structlog

from workspace.limits import LimitExceededError, RepoLimits, TreeStats, enforce_limits, measure_tree

logger = structlog.get_logger(__name__)

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

_SAFE_CONFIG: Final[tuple[tuple[str, str], ...]] = (
    ("core.hooksPath", "/dev/null"),
    ("core.symlinks", "false"),
    ("core.fsmonitor", "false"),
    ("init.templateDir", ""),
    ("protocol.file.allow", "never"),
    ("protocol.ext.allow", "never"),
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("filter.lfs.smudge", ""),
    ("filter.lfs.process", ""),
    ("filter.lfs.required", "false"),
    ("advice.detachedHead", "false"),
    ("credential.helper", ""),
)


class CloneError(Exception):
    """A clone failed or was rejected. ``code`` is stable and shown to users."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CloneConfig:
    limits: RepoLimits = field(default_factory=RepoLimits)
    timeout_seconds: float = 300.0
    allowed_url_prefixes: tuple[str, ...] = ("https://github.com/",)
    size_poll_seconds: float = 0.5
    git_binary: str = "git"


@dataclass(frozen=True)
class CloneResult:
    path: Path
    commit_sha: str
    stats: TreeStats


def _git_env(home: Path, extra_config: list[tuple[str, str]]) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_PROTOCOL_FROM_USER": "0",
    }
    for key in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    if "SSL_CERT_FILE" in env:
        env["GIT_SSL_CAINFO"] = env["SSL_CERT_FILE"]
    for index, (key, value) in enumerate(extra_config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(len(extra_config))
    return env


def _config_args() -> list[str]:
    args: list[str] = []
    for key, value in _SAFE_CONFIG:
        args += ["-c", f"{key}={value}"]
    return args


class GitCloner:
    def __init__(self, config: CloneConfig | None = None) -> None:
        self._config = config or CloneConfig()
        self._git = self._config.git_binary

    def validate_url(self, url: str) -> None:
        if not any(url.startswith(p) for p in self._config.allowed_url_prefixes):
            raise CloneError("url_not_allowed", f"clone URL not allowed: {url}")
        if any(c in url for c in ("\n", "\r", " ", "@")):
            raise CloneError("url_not_allowed", "clone URL contains forbidden characters")

    async def clone(
        self, url: str, dest: Path, ref: str | None = None, token: str | None = None
    ) -> CloneResult:
        """Shallow-fetch ``ref`` (branch, tag or 40-char SHA; default HEAD) into ``dest``."""
        self.validate_url(url)
        if ref is not None and not _valid_ref(ref):
            raise CloneError("invalid_ref", f"invalid git ref: {ref!r}")
        await asyncio.to_thread(_prepare_destination, dest)

        extra: list[tuple[str, str]] = []
        if token is not None:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            extra.append(
                (
                    f"http.{url.split('/', 3)[0]}//{url.split('/', 3)[2]}/.extraheader",
                    f"AUTHORIZATION: basic {basic}",
                )
            )

        home = Path(tempfile.mkdtemp(prefix="devagent-githome-"))
        try:
            await run_git(["init", "--quiet", str(dest)], dest.parent, home, git_binary=self._git)
            await run_git(["remote", "add", "origin", url], dest, home, git_binary=self._git)
            await self._fetch(dest, home, ref or "HEAD", extra)
            await run_git(
                ["checkout", "--quiet", "--detach", "FETCH_HEAD"], dest, home, git_binary=self._git
            )
            sha = (await run_git(["rev-parse", "HEAD"], dest, home, git_binary=self._git)).strip()
            stats = await asyncio.to_thread(_check_limits, dest, self._config.limits)
        except CloneError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(home, ignore_errors=True)
        logger.info("repository_cloned", url=url, sha=sha, files=stats.files, bytes=stats.bytes)
        return CloneResult(path=dest, commit_sha=sha, stats=stats)

    async def _fetch(self, dest: Path, home: Path, ref: str, extra: list[tuple[str, str]]) -> None:
        argv = [
            self._config.git_binary,
            *_config_args(),
            "fetch",
            "--quiet",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            ref,
        ]
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=dest,
            env=_git_env(home, extra),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = asyncio.ensure_future(process.communicate())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.timeout_seconds
        try:
            while not communicate.done():
                if loop.time() > deadline:
                    raise _timeout(self._config.timeout_seconds)
                # .git grows while objects download; stop early if it gets too big.
                await asyncio.to_thread(_check_limits, dest / ".git", self._config.limits)
                await asyncio.wait({communicate}, timeout=self._config.size_poll_seconds)
        except CloneError:
            process.kill()
            await process.wait()
            communicate.cancel()
            raise
        _stdout, stderr = communicate.result()
        if process.returncode != 0:
            raise CloneError("clone_failed", _clean(stderr))


async def run_git(
    args: list[str],
    cwd: Path,
    home: Path,
    *,
    extra_config: list[tuple[str, str]] | None = None,
    git_binary: str = "git",
    timeout_seconds: float = 60.0,
) -> str:
    """Run one git command with the hardened config and environment."""
    process = await asyncio.create_subprocess_exec(
        git_binary,
        *_config_args(),
        *args,
        cwd=cwd,
        env=_git_env(home, extra_config or []),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await process.communicate()
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise CloneError("clone_timeout", f"git {args[0]} timed out") from exc
    if process.returncode != 0:
        raise CloneError("clone_failed", f"git {args[0]}: {_clean(stderr)}")
    return stdout.decode("utf-8", "replace")


def _valid_ref(ref: str) -> bool:
    if ref.startswith("-") or ".." in ref:
        return False
    return bool(_SHA.match(ref) or _REF.match(ref))


def _prepare_destination(dest: Path) -> None:
    if dest.exists() and any(dest.iterdir()):
        raise CloneError("destination_not_empty", f"{dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)


def _check_limits(root: Path, limits: RepoLimits) -> TreeStats:
    try:
        return enforce_limits(root, limits)
    except LimitExceededError as exc:
        raise CloneError("repository_too_large", str(exc)) from exc


def _timeout(seconds: float) -> CloneError:
    return CloneError("clone_timeout", f"fetch exceeded {seconds:g}s")


def _clean(stderr: bytes) -> str:
    text = stderr.decode("utf-8", "replace").strip()
    # Never echo credentials that a remote might reflect back.
    return re.sub(r"(?i)authorization:\s*\S+\s+\S+", "AUTHORIZATION: [REDACTED]", text)[:500]


async def copy_local_repository(
    source: Path, dest: Path, limits: RepoLimits, git_binary: str = "git"
) -> CloneResult:
    """Copy a local sample repo (no ``.git``) and commit it, giving a real base commit.

    Symlinks are copied as links but the tree is checked against limits first; tools
    enforce the workspace boundary on every access (Phase 4).
    """
    await asyncio.to_thread(_check_limits, source, limits)
    await asyncio.to_thread(_prepare_destination, dest)
    await asyncio.to_thread(
        shutil.copytree,
        source,
        dest,
        symlinks=True,
        # Local build and test leftovers are never part of a sample's source.
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info", ".mypy_cache"
        ),
        dirs_exist_ok=True,
    )
    home = Path(tempfile.mkdtemp(prefix="devagent-githome-"))
    try:
        await run_git(["init", "--quiet"], dest, home, git_binary=git_binary)
        await run_git(["add", "-A"], dest, home, git_binary=git_binary)
        identity = ["-c", "user.name=DevAgent", "-c", "user.email=devagent@localhost"]
        commit = ["commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", "base"]
        await run_git([*identity, *commit], dest, home, git_binary=git_binary)
        sha = (await run_git(["rev-parse", "HEAD"], dest, home, git_binary=git_binary)).strip()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    stats = await asyncio.to_thread(measure_tree, dest)
    return CloneResult(path=dest, commit_sha=sha, stats=stats)
