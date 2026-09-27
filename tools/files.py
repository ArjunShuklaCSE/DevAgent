"""File tools: list_tree, search_files, read_file, edit_file, create_file."""

import asyncio
import difflib
import hashlib
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import ClassVar

from pydantic import Field

from core.tools import ChangeType, ToolCapability
from tools.base import BaseTool, FileChange, ToolContext, ToolInput, ToolOutput
from tools.errors import ToolError
from tools.git import list_files
from tools.paths import WorkspacePaths
from tools.sensitive import classify_path

# Directories the agent never needs to look into.
SKIPPED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "third_party",
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
BINARY_SNIFF_BYTES = 8192
MAX_READ_BYTES = 5 * 1024 * 1024
MAX_LINE_CHARS = 500
MAX_WRITE_BYTES = 256 * 1024


def is_skipped(relative: str) -> bool:
    parts = PurePosixPath(relative).parts[:-1]
    return any(part in SKIPPED_DIRS or part.endswith(".egg-info") for part in parts)


def is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\x00" in handle.read(BINARY_SNIFF_BYTES)


async def visible_files(ctx: ToolContext) -> list[str]:
    """Files the agent can see: git-visible, not vendored, not binary."""
    files = await list_files(ctx.repo)

    def keep(rel: str) -> bool:
        return not is_skipped(rel) and not is_binary(ctx.repo / rel)

    return [rel for rel in files if await asyncio.to_thread(keep, rel)]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unified_diff(path: str, before: str | None, after: str) -> str:
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff
    )


def check_writable(rel: str, ctx: ToolContext) -> str:
    """Return the path class, raising if the path may not be written."""
    path_class = classify_path(rel)
    if path_class == "blocked":
        raise ToolError("blocked_path", f"{rel} is inside .git and can never be edited")
    if path_class == "protected" and rel not in ctx.allowed_protected_paths:
        raise ToolError(
            "protected_path",
            f"{rel} is CI configuration or a lockfile; it can only be changed when the plan "
            "names it and explains why",
        )
    return path_class


# --------------------------------------------------------------------------- list_tree
class ListTreeInput(ToolInput):
    path: str = Field(default=".", description="directory, relative to the repository root")
    depth: int = Field(default=2, ge=1, le=6, description="how many levels to expand")


class ListTreeTool(BaseTool[ListTreeInput]):
    name: ClassVar[str] = "list_tree"
    description: ClassVar[str] = (
        "List files and directories under a path, respecting .gitignore and skipping "
        "binaries and vendored directories. Deeper directories are summarized with a file count."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = ListTreeInput
    max_entries: ClassVar[int] = 400

    async def run(self, args: ListTreeInput, ctx: ToolContext) -> ToolOutput:
        paths = WorkspacePaths(ctx.repo)
        base = paths.relative(paths.resolve(args.path))
        if not (ctx.repo / base).is_dir():
            raise ToolError("not_a_directory", f"{args.path!r} is not a directory")
        prefix = "" if base == "." else base + "/"
        files = [f[len(prefix) :] for f in await visible_files(ctx) if f.startswith(prefix)]

        entries: set[str] = set()
        collapsed: Counter[str] = Counter()
        for rel in files:
            parts = rel.split("/")
            if len(parts) <= args.depth:
                entries.add(rel)
                entries.update("/".join(parts[:i]) + "/" for i in range(1, len(parts)))
            else:
                folder = "/".join(parts[: args.depth]) + "/"
                collapsed[folder] += 1
                entries.update("/".join(parts[:i]) + "/" for i in range(1, args.depth + 1))
        ordered = sorted(entries)
        lines = []
        for entry in ordered[: self.max_entries]:
            depth = entry.rstrip("/").count("/")
            name = entry.rstrip("/").split("/")[-1] + ("/" if entry.endswith("/") else "")
            suffix = f"  ({collapsed[entry]} files below)" if entry in collapsed else ""
            lines.append("  " * depth + name + suffix)
        if len(ordered) > self.max_entries:
            lines.append(
                f"[... {len(ordered) - self.max_entries} more entries; narrow the path ...]"
            )
        header = f"{base}/ ({len(files)} files)"
        return ToolOutput(
            text="\n".join([header, *lines]) if files else f"{header}\n(empty)",
            data={"path": base, "files": len(files), "entries": len(ordered)},
        )


# --------------------------------------------------------------------------- search_files
class SearchFilesInput(ToolInput):
    glob: str = Field(
        min_length=1,
        max_length=200,
        description="glob matched against the relative path, e.g. '**/test_*.py'",
    )
    max_results: int = Field(default=100, ge=1, le=500)


class SearchFilesTool(BaseTool[SearchFilesInput]):
    name: ClassVar[str] = "search_files"
    description: ClassVar[str] = (
        "Find files by name. The glob is matched against paths relative to the repository "
        "root; a pattern without '/' also matches the file name alone."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = SearchFilesInput

    async def run(self, args: SearchFilesInput, ctx: ToolContext) -> ToolOutput:
        pattern = args.glob.removeprefix("./")
        globs = {pattern, pattern.replace("**/", "")}
        matches = [
            rel
            for rel in await visible_files(ctx)
            if any(fnmatchcase(rel, g) for g in globs)
            or ("/" not in pattern and fnmatchcase(rel.rsplit("/", 1)[-1], pattern))
        ]
        shown = matches[: args.max_results]
        more = len(matches) - len(shown)
        text = "\n".join(shown) if shown else f"no files match {args.glob!r}"
        if more > 0:
            text += f"\n[... {more} more matches ...]"
        return ToolOutput(text=text, data={"matches": len(matches)})


# --------------------------------------------------------------------------- read_file
class ReadFileInput(ToolInput):
    path: str = Field(description="file path relative to the repository root")
    start_line: int = Field(default=1, ge=1, description="first line to read (1-based)")
    end_line: int | None = Field(
        default=None, ge=1, description="last line to read (inclusive); default start_line+199"
    )


class ReadFileTool(BaseTool[ReadFileInput]):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read a line range of a text file (at most 400 lines per call). Lines are prefixed "
        "with their number. Use search_text or find_symbol first to find the right range."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = ReadFileInput
    max_lines: ClassVar[int] = 400
    default_lines: ClassVar[int] = 200
    max_output_chars: ClassVar[int] = 40_000

    async def run(self, args: ReadFileInput, ctx: ToolContext) -> ToolOutput:
        paths = WorkspacePaths(ctx.repo)
        path = paths.resolve(args.path)
        rel = paths.relative(path)
        end = args.end_line or args.start_line + self.default_lines - 1
        if end < args.start_line:
            raise ToolError("invalid_range", "end_line is before start_line")
        if end - args.start_line + 1 > self.max_lines:
            raise ToolError("range_too_large", f"read at most {self.max_lines} lines per call")
        lines, total = await asyncio.to_thread(_read_lines, path, args.start_line, end)
        if total == 0:
            return ToolOutput(text=f"{rel} is empty", data={"path": rel, "total_lines": 0})
        if args.start_line > total:
            raise ToolError("invalid_range", f"{rel} has only {total} lines")
        width = len(str(min(end, total)))
        body = "\n".join(
            f"{number:>{width}}| {text}" for number, text in enumerate(lines, args.start_line)
        )
        last = args.start_line + len(lines) - 1
        footer = f"\n[lines {args.start_line}-{last} of {total}]"
        return ToolOutput(
            text=body + footer,
            data={
                "path": rel,
                "start_line": args.start_line,
                "end_line": last,
                "total_lines": total,
            },
        )


def _read_lines(path: Path, start: int, end: int) -> tuple[list[str], int]:
    if not path.is_file():
        raise ToolError("not_a_file", f"{path.name} is not a regular file")
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolError("file_too_large", f"{path.name} is larger than {MAX_READ_BYTES} bytes")
    if is_binary(path):
        raise ToolError("binary_file", f"{path.name} looks like a binary file")
    selected: list[str] = []
    total = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for total, line in enumerate(handle, 1):
            if start <= total <= end:
                text = line.rstrip("\n").rstrip("\r")
                if len(text) > MAX_LINE_CHARS:
                    text = text[:MAX_LINE_CHARS] + f" [... {len(text) - MAX_LINE_CHARS} chars cut]"
                selected.append(text)
    return selected, total


# --------------------------------------------------------------------------- edit_file
class EditFileInput(ToolInput):
    path: str = Field(description="file path relative to the repository root")
    old_str: str = Field(
        min_length=1, description="exact text to replace; must occur exactly once in the file"
    )
    new_str: str = Field(description="replacement text")


class EditFileTool(BaseTool[EditFileInput]):
    name: ClassVar[str] = "edit_file"
    description: ClassVar[str] = (
        "Replace one exact occurrence of old_str with new_str. Fails if old_str occurs zero "
        "times or more than once; include enough surrounding lines to make it unique. "
        "Whitespace and indentation must match exactly."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.WRITE_WORKSPACE
    input_model = EditFileInput

    async def run(self, args: EditFileInput, ctx: ToolContext) -> ToolOutput:
        paths = WorkspacePaths(ctx.repo)
        path = paths.resolve_for_write(args.path)
        rel = paths.relative(path)
        path_class = check_writable(rel, ctx)
        if not path.is_file():
            raise ToolError("not_found", f"{rel} does not exist; use create_file for new files")
        change = await asyncio.to_thread(_apply_edit, path, rel, args, path_class)
        added = sum(
            1
            for line in change.diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1
            for line in change.diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        return ToolOutput(
            text=f"edited {rel} (+{added} -{removed})\n{change.diff}",
            data={"path": rel, "added": added, "removed": removed, "path_class": path_class},
            changes=(change,),
        )


def _apply_edit(path: Path, rel: str, args: EditFileInput, path_class: str) -> FileChange:
    raw = path.read_bytes()
    if len(raw) > MAX_READ_BYTES or b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        raise ToolError("binary_file", f"{rel} is binary or too large to edit")
    try:
        before = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("not_utf8", f"{rel} is not valid UTF-8") from exc
    count = before.count(args.old_str)
    if count == 0:
        raise ToolError(
            "no_match",
            f"old_str was not found in {rel}; read the file again and copy the text exactly",
        )
    if count > 1:
        lines = _match_lines(before, args.old_str)
        raise ToolError(
            "ambiguous_match",
            f"old_str occurs {count} times in {rel} (lines {', '.join(map(str, lines))}); "
            "include more surrounding context so it matches once",
        )
    if args.old_str == args.new_str:
        raise ToolError("no_change", "old_str and new_str are identical")
    after = before.replace(args.old_str, args.new_str, 1)
    encoded = after.encode("utf-8")
    if len(encoded) > MAX_READ_BYTES:
        raise ToolError("file_too_large", "the edited file would be too large")
    path.write_bytes(encoded)
    return FileChange(
        path=rel,
        change_type=ChangeType.MODIFY,
        before_sha256=sha256_bytes(raw),
        after_sha256=sha256_bytes(encoded),
        diff=unified_diff(rel, before, after),
        path_class=path_class,
    )


def _match_lines(text: str, needle: str) -> list[int]:
    lines: list[int] = []
    start = 0
    while (index := text.find(needle, start)) != -1 and len(lines) < 10:  # noqa: PLR2004
        lines.append(text.count("\n", 0, index) + 1)
        start = index + 1
    return lines


# --------------------------------------------------------------------------- create_file
class CreateFileInput(ToolInput):
    path: str = Field(description="new file path relative to the repository root")
    content: str = Field(max_length=MAX_WRITE_BYTES, description="full file content")


class CreateFileTool(BaseTool[CreateFileInput]):
    name: ClassVar[str] = "create_file"
    description: ClassVar[str] = (
        "Create a new file (for example a reproduction test). Fails if the file already "
        "exists; use edit_file to change existing files."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.WRITE_WORKSPACE
    input_model = CreateFileInput

    async def run(self, args: CreateFileInput, ctx: ToolContext) -> ToolOutput:
        paths = WorkspacePaths(ctx.repo)
        path = paths.resolve_for_write(args.path)
        rel = paths.relative(path)
        path_class = check_writable(rel, ctx)
        change = await asyncio.to_thread(_create, paths, path, rel, args.content, path_class)
        lines = args.content.count("\n") + (0 if args.content.endswith("\n") else 1)
        return ToolOutput(
            text=f"created {rel} ({lines} lines)",
            data={"path": rel, "lines": lines, "path_class": path_class},
            changes=(change,),
        )


def _create(
    paths: WorkspacePaths, path: Path, rel: str, content: str, path_class: str
) -> FileChange:
    if path.exists() or path.is_symlink():
        raise ToolError("already_exists", f"{rel} already exists; use edit_file")
    # Every existing ancestor must be a real directory inside the workspace.
    for parent in reversed(path.relative_to(paths.root).parents):
        directory = paths.root / parent
        if directory.is_symlink():
            raise ToolError("symlink_write_denied", f"{paths.relative(directory)} is a symlink")
        if directory.exists() and not directory.is_dir():
            raise ToolError("not_a_directory", f"{paths.relative(directory)} is not a directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with path.open("xb") as handle:  # exclusive: never overwrite a file created meanwhile
        handle.write(encoded)
    return FileChange(
        path=rel,
        change_type=ChangeType.CREATE,
        before_sha256=None,
        after_sha256=sha256_bytes(encoded),
        diff=unified_diff(rel, None, content),
        path_class=path_class,
    )
