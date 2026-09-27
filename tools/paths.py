"""Workspace path safety, applied by every file tool (spec 5).

Paths from the model are resolved against the workspace root. Absolute paths are
accepted only under ``/workspace`` (how the sandbox sees the same tree); ``..``
segments, NUL bytes and anything that resolves outside the root, including through a
symlink, are rejected. Writes never go through a symlink, even one that stays inside.
"""

from pathlib import Path, PurePosixPath

from tools.errors import ToolError

SANDBOX_ROOT = PurePosixPath("/workspace")
MAX_PATH_LENGTH = 1024


class WorkspacePaths:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str, *, must_exist: bool = True) -> Path:
        pure = self._normalize(path)
        candidate = self._root.joinpath(*pure.parts)
        crossed_symlink = self._crosses_symlink(pure)
        real = candidate.resolve(strict=False)
        if not real.is_relative_to(self._root):
            if crossed_symlink:
                raise ToolError("symlink_escape", f"{path!r} resolves outside the workspace")
            raise ToolError("path_outside_workspace", f"{path!r} is outside the workspace")
        if must_exist and not real.exists():
            raise ToolError("not_found", f"{path!r} does not exist")
        return real

    def resolve_for_write(self, path: str) -> Path:
        pure = self._normalize(path)
        candidate = self._root.joinpath(*pure.parts)
        if candidate.is_symlink():
            raise ToolError("symlink_write_denied", f"{path!r} is a symlink; edit its target")
        return self.resolve(path, must_exist=False)

    def relative(self, absolute: Path) -> str:
        rel = absolute.relative_to(self._root).as_posix()
        return rel or "."

    def _normalize(self, path: str) -> PurePosixPath:
        if not path or "\x00" in path or len(path) > MAX_PATH_LENGTH:
            raise ToolError("invalid_path", "path is empty, too long or contains NUL")
        pure = PurePosixPath(path.replace("\\", "/"))
        if pure.is_absolute():
            if not pure.is_relative_to(SANDBOX_ROOT):
                raise ToolError("path_outside_workspace", f"{path!r} is outside the workspace")
            pure = pure.relative_to(SANDBOX_ROOT)
        if ".." in pure.parts:
            raise ToolError("path_outside_workspace", f"{path!r} contains '..'")
        return pure

    def _crosses_symlink(self, pure: PurePosixPath) -> bool:
        current = self._root
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False
