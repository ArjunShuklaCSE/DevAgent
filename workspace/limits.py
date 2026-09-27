"""Workspace size and file-count accounting without following symlinks."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoLimits:
    max_bytes: int = 200 * 1024 * 1024
    max_files: int = 20_000


@dataclass(frozen=True)
class TreeStats:
    bytes: int
    files: int


class LimitExceededError(Exception):
    def __init__(self, stats: TreeStats, limits: RepoLimits) -> None:
        super().__init__(
            f"repository exceeds limits: {stats.files} files / {stats.bytes} bytes "
            f"(max {limits.max_files} files / {limits.max_bytes} bytes)"
        )
        self.stats = stats
        self.limits = limits


def measure_tree(root: Path, *, stop_after: RepoLimits | None = None) -> TreeStats:
    """Sum regular-file sizes under ``root`` (lstat, symlinks not followed).

    With ``stop_after``, raises ``LimitExceededError`` as soon as a limit is crossed so
    a huge tree is not walked to the end.
    """
    total_bytes = 0
    total_files = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames + [d for d in dirnames if Path(dirpath, d).is_symlink()]:
            st = Path(dirpath, name).lstat()
            total_files += 1
            total_bytes += st.st_size
            if stop_after is not None and (
                total_files > stop_after.max_files or total_bytes > stop_after.max_bytes
            ):
                raise LimitExceededError(TreeStats(total_bytes, total_files), stop_after)
    return TreeStats(total_bytes, total_files)


def enforce_limits(root: Path, limits: RepoLimits) -> TreeStats:
    return measure_tree(root, stop_after=limits)
