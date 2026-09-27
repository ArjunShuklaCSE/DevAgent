"""Code search tools: search_text (ripgrep), find_symbol and find_references (ast)."""

import ast
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from core.tools import ToolCapability
from tools.base import BaseTool, ToolContext, ToolInput, ToolOutput
from tools.errors import ToolError
from tools.files import SKIPPED_DIRS, visible_files
from tools.paths import WorkspacePaths

RG_TIMEOUT_SECONDS = 20.0
MAX_AST_FILE_BYTES = 1024 * 1024
MAX_AST_FILES = 5000
_IDENTIFIER_PATH = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"


def ripgrep_binary() -> str:
    """The pinned ``rg`` from the ``ripgrep`` wheel, else one on PATH."""
    bundled = Path(sys.prefix) / "bin" / "rg"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("rg")
    if found is None:
        raise ToolError("search_unavailable", "ripgrep (rg) is not installed")
    return found


# --------------------------------------------------------------------------- search_text
class SearchTextInput(ToolInput):
    pattern: str = Field(
        min_length=1, max_length=500, description="regular expression (Rust regex syntax)"
    )
    path: str = Field(default=".", description="file or directory to search, relative to the root")
    max_results: int = Field(default=50, ge=1, le=200)
    case_sensitive: bool = True
    fixed_string: bool = Field(default=False, description="treat pattern as a literal string")


class SearchTextTool(BaseTool[SearchTextInput]):
    name: ClassVar[str] = "search_text"
    description: ClassVar[str] = (
        "Search file contents with ripgrep. Returns 'path:line: text' matches, capped at "
        "max_results. Respects .gitignore; skips binaries and vendored directories."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = SearchTextInput

    async def run(self, args: SearchTextInput, ctx: ToolContext) -> ToolOutput:
        paths = WorkspacePaths(ctx.repo)
        target = paths.relative(paths.resolve(args.path))
        argv = [
            ripgrep_binary(),
            "--no-config",
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-columns=300",
            "--max-columns-preview",
            "--max-filesize=1M",
            "--no-follow",
            *(f"--glob=!{d}/" for d in sorted(SKIPPED_DIRS)),
            "--glob=!*.egg-info/",
        ]
        if not args.case_sensitive:
            argv.append("--ignore-case")
        if args.fixed_string:
            argv.append("--fixed-strings")
        argv += ["--regexp", args.pattern]
        if target != ".":  # searching "." would prefix every path with "./"
            argv += ["--", target]
        lines, total_seen, stderr, code = await _run_capped(argv, ctx.repo, args.max_results)
        if code == 2 and not lines:  # noqa: PLR2004 - rg: 2 = error
            raise ToolError("invalid_pattern", stderr.strip()[:500] or "ripgrep failed")
        if not lines:
            return ToolOutput(
                text=f"no matches for {args.pattern!r} in {target}", data={"matches": 0}
            )
        text = "\n".join(lines)
        if total_seen > len(lines):
            text += "\n[... more matches not shown; refine the pattern or path ...]"
        return ToolOutput(
            text=text, data={"matches": len(lines), "capped": total_seen > len(lines)}
        )


async def _run_capped(argv: list[str], cwd: Path, limit: int) -> tuple[list[str], int, str, int]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover - PIPE is set
        raise ToolError("search_unavailable", "could not read ripgrep output")
    lines: list[str] = []
    seen = 0
    try:
        async with asyncio.timeout(RG_TIMEOUT_SECONDS):
            async for raw in process.stdout:
                seen += 1
                if len(lines) < limit:
                    lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
                else:
                    break  # one extra line tells us there were more
            if process.returncode is None and seen > limit:
                process.kill()
            stderr = (await process.stderr.read()).decode("utf-8", "replace")
            code = await process.wait()
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ToolError(
            "search_timeout", f"search took longer than {RG_TIMEOUT_SECONDS:g}s"
        ) from exc
    return lines, seen, stderr, code


# --------------------------------------------------------------------------- ast symbols
@dataclass(frozen=True)
class SymbolHit:
    path: str
    line: int
    kind: str
    name: str
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} {self.name} | {self.text.strip()[:200]}"


class _DefinitionVisitor(ast.NodeVisitor):
    """Collect (line, kind, qualified name) for classes, functions and module/class variables."""

    def __init__(self) -> None:
        self.scope: list[tuple[Literal["class", "function"], str]] = []
        self.found: list[tuple[int, str, str]] = []

    def _define(self, line: int, kind: str, name: str) -> None:
        qualified = ".".join([*(part for _, part in self.scope), name])
        self.found.append((line, kind, qualified))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._define(node.lineno, "class", node.name)
        self.scope.append(("class", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        in_class = bool(self.scope) and self.scope[-1][0] == "class"
        self._define(node.lineno, "method" if in_class else "function", node.name)
        self.scope.append(("function", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._in_function():
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._define(node.lineno, "variable", target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self._in_function() and isinstance(node.target, ast.Name):
            self._define(node.lineno, "variable", node.target.id)
        self.generic_visit(node)

    def _in_function(self) -> bool:
        return any(kind == "function" for kind, _ in self.scope)


def _definitions(tree: ast.Module) -> list[tuple[int, str, str]]:
    visitor = _DefinitionVisitor()
    visitor.visit(tree)
    return visitor.found


def _references(tree: ast.Module, name: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            hits.append((node.lineno, "name"))
        elif isinstance(node, ast.Attribute) and node.attr == name:
            hits.append((node.lineno, "attribute"))
        elif isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == name or alias.asname == name:
                    hits.append((node.lineno, "import"))
        elif isinstance(node, ast.keyword) and node.arg == name:
            hits.append((getattr(node, "lineno", 0), "keyword"))
    return sorted(set(hits))


def _parse(path: Path) -> tuple[ast.Module, list[str]] | None:
    try:
        if path.stat().st_size > MAX_AST_FILE_BYTES:
            return None
        source = path.read_text(encoding="utf-8", errors="replace")
        return ast.parse(source, filename=path.name), source.splitlines()
    except (SyntaxError, ValueError, RecursionError, MemoryError, OSError):
        return None  # unparseable files are skipped, not fatal


async def _python_files(ctx: ToolContext) -> list[str]:
    files = [f for f in await visible_files(ctx) if f.endswith(".py")]
    return files[:MAX_AST_FILES]


class SymbolInput(ToolInput):
    name: str = Field(
        pattern=_IDENTIFIER_PATH,
        max_length=200,
        description="identifier, optionally qualified: 'chunk', 'Wallet.withdraw'",
    )
    max_results: int = Field(default=50, ge=1, le=200)


class FindSymbolTool(BaseTool[SymbolInput]):
    name: ClassVar[str] = "find_symbol"
    description: ClassVar[str] = (
        "Find where a Python class, function, method or module-level variable is defined "
        "(parsed with ast, no code is executed). Accepts 'name' or 'Class.name'."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = SymbolInput

    async def run(self, args: SymbolInput, ctx: ToolContext) -> ToolOutput:
        hits = await asyncio.to_thread(self._scan, ctx.repo, await _python_files(ctx), args.name)
        return _render(hits, args.max_results, f"no definition of {args.name!r} found")

    @staticmethod
    def _scan(root: Path, files: list[str], wanted: str) -> list[SymbolHit]:
        hits = []
        for rel in files:
            parsed = _parse(root / rel)
            if parsed is None:
                continue
            tree, lines = parsed
            for line, kind, qualified in _definitions(tree):
                if qualified == wanted or qualified.endswith("." + wanted):
                    text = lines[line - 1] if 0 < line <= len(lines) else ""
                    hits.append(SymbolHit(rel, line, kind, qualified, text))
        return hits


class FindReferencesTool(BaseTool[SymbolInput]):
    name: ClassVar[str] = "find_references"
    description: ClassVar[str] = (
        "Find usages of a Python name (calls, attribute access, imports, keyword arguments) "
        "across the repository, parsed with ast. For 'Class.name' the last part is searched."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = SymbolInput

    async def run(self, args: SymbolInput, ctx: ToolContext) -> ToolOutput:
        name = args.name.split(".")[-1]
        hits = await asyncio.to_thread(self._scan, ctx.repo, await _python_files(ctx), name)
        return _render(hits, args.max_results, f"no references to {name!r} found")

    @staticmethod
    def _scan(root: Path, files: list[str], name: str) -> list[SymbolHit]:
        hits = []
        for rel in files:
            parsed = _parse(root / rel)
            if parsed is None:
                continue
            tree, lines = parsed
            for line, kind in _references(tree, name):
                text = lines[line - 1] if 0 < line <= len(lines) else ""
                hits.append(SymbolHit(rel, line, kind, name, text))
        return hits


def _render(hits: list[SymbolHit], limit: int, empty: str) -> ToolOutput:
    if not hits:
        return ToolOutput(text=empty, data={"matches": 0})
    shown = hits[:limit]
    text = "\n".join(hit.render() for hit in shown)
    if len(hits) > limit:
        text += f"\n[... {len(hits) - limit} more ...]"
    return ToolOutput(
        text=text,
        data={
            "matches": len(hits),
            "locations": [{"path": h.path, "line": h.line, "kind": h.kind} for h in shown],
        },
    )
