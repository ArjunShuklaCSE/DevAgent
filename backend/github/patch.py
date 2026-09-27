"""Parse a ``git diff`` into per-file changes and render a ``git am`` patch file."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Literal

ChangeKind = Literal["add", "modify", "delete", "rename"]

_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


class PatchError(Exception):
    code = "invalid_patch"


@dataclass
class FileChange:
    old_path: str | None
    new_path: str | None
    kind: ChangeKind = "modify"
    new_mode: str | None = None
    binary: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        path = self.new_path or self.old_path
        assert path is not None  # noqa: S101 - one of them is always set by the parser
        return path


def _check_path(path: str) -> str:
    if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise PatchError(f"unsafe path in diff: {path!r}")
    if path == ".git" or path.startswith(".git/"):
        raise PatchError(f"diff touches git metadata: {path!r}")
    return path


def parse_diff(diff: str) -> list[FileChange]:
    """Split a unified ``git diff`` into file changes. Rejects unsafe paths."""
    changes: list[FileChange] = []
    current: FileChange | None = None
    for line in diff.splitlines():
        header = _HEADER.match(line)
        if header:
            current = FileChange(
                old_path=_check_path(header["a"]), new_path=_check_path(header["b"])
            )
            changes.append(current)
            current.lines.append(line)
            continue
        if current is None:
            continue
        current.lines.append(line)
        if line.startswith("new file mode "):
            current.kind = "add"
            current.old_path = None
            current.new_mode = line.removeprefix("new file mode ").strip()
        elif line.startswith("deleted file mode "):
            current.kind = "delete"
            current.new_path = None
        elif line.startswith("new mode "):
            current.new_mode = line.removeprefix("new mode ").strip()
        elif line.startswith("rename from "):
            current.kind = "rename"
            current.old_path = _check_path(line.removeprefix("rename from "))
        elif line.startswith("rename to "):
            current.new_path = _check_path(line.removeprefix("rename to "))
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            current.binary = True
    if diff.strip() and not changes:
        raise PatchError("diff has no file headers")
    return changes


def render_patch(
    diff: str,
    *,
    subject: str,
    body: str,
    author_name: str,
    author_email: str,
    date: datetime | None = None,
) -> str:
    """A ``git format-patch`` style file that ``git am`` accepts."""
    when = date or datetime.now(UTC)
    stat = "\n".join(f" {c.path}" for c in parse_diff(diff))
    return (
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        f"From: {author_name} <{author_email}>\n"
        f"Date: {format_datetime(when)}\n"
        f"Subject: [PATCH] {subject.splitlines()[0] if subject else 'DevAgent fix'}\n"
        "\n"
        f"{body.strip()}\n"
        "---\n"
        f"{stat}\n"
        "\n"
        f"{diff if diff.endswith(chr(10)) else diff + chr(10)}"
        "-- \n"
        "DevAgent\n"
    )
