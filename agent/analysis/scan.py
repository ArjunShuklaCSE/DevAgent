"""Bounded, read-only view of a repository tree used by language adapters."""

import os
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final

SKIP_DIRS: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "site-packages",
        ".idea",
        ".vscode",
        "vendor",
        "third_party",
    }
)
EXTENSIONS: Final = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
}
MAX_READ_BYTES: Final = 512 * 1024


@dataclass
class RepoTree:
    """Paths (POSIX, relative) of regular files; reads are size-capped and never follow links."""

    root: Path
    files: list[str]
    language_counts: Counter[str] = field(default_factory=Counter)
    _cache: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def scan(cls, root: Path, max_files: int = 20_000) -> "RepoTree":
        files: list[str] = []
        counts: Counter[str] = Counter()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
            )
            for name in sorted(filenames):
                path = Path(dirpath, name)
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                files.append(rel)
                language = EXTENSIONS.get(PurePosixPath(name).suffix)
                if language:
                    counts[language] += 1
                if len(files) >= max_files:
                    return cls(root=root, files=files, language_counts=counts)
        return cls(root=root, files=files, language_counts=counts)

    def __post_init__(self) -> None:
        self._file_set = frozenset(self.files)

    def exists(self, rel: str) -> bool:
        return rel in self._file_set

    def read_text(self, rel: str) -> str | None:
        if rel in self._cache:
            return self._cache[rel]
        text: str | None = None
        if self.exists(rel):
            path = self.root / rel
            if path.stat().st_size <= MAX_READ_BYTES:
                text = path.read_text("utf-8", errors="replace")
        self._cache[rel] = text
        return text

    def read_toml(self, rel: str) -> dict[str, Any]:
        text = self.read_text(rel)
        if text is None:
            return {}
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}

    def glob_names(self, *names: str) -> list[str]:
        wanted = set(names)
        return [f for f in self.files if PurePosixPath(f).name in wanted]

    def top_level_dirs(self) -> list[str]:
        return sorted({f.split("/", 1)[0] for f in self.files if "/" in f})
