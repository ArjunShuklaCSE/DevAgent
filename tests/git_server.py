"""A real smart-HTTP git server for tests (``git http-backend`` behind wsgiref).

Lets clone tests exercise the same HTTP protocol path as GitHub without network access.
"""

import os
import subprocess
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

GIT_EXEC = subprocess.run(
    ["git", "--exec-path"], capture_output=True, text=True, check=True
).stdout.strip()


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return


class _ThreadingServer(WSGIServer):
    daemon_threads = True

    def process_request(self, request: Any, client_address: Any) -> None:
        thread = threading.Thread(target=self._handle, args=(request, client_address), daemon=True)
        thread.start()

    def _handle(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        finally:
            self.shutdown_request(request)


def _app(root: Path, seen_headers: list[dict[str, str]]):  # type: ignore[no-untyped-def]
    def app(environ: dict[str, Any], start_response: Any) -> Iterable[bytes]:
        seen_headers.append({k: v for k, v in environ.items() if k.startswith("HTTP_")})
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        env = {
            "GIT_PROJECT_ROOT": str(root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": environ.get("PATH_INFO", ""),
            "QUERY_STRING": environ.get("QUERY_STRING", ""),
            "REQUEST_METHOD": environ["REQUEST_METHOD"],
            "CONTENT_TYPE": environ.get("CONTENT_TYPE", ""),
            "CONTENT_LENGTH": str(len(body)),
            "GIT_PROTOCOL": environ.get("HTTP_GIT_PROTOCOL", ""),
            "REMOTE_ADDR": "127.0.0.1",
            "PATH": os.environ["PATH"],
        }
        out = subprocess.run(
            [f"{GIT_EXEC}/git-http-backend"], input=body, env=env, capture_output=True, check=False
        ).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = "200 OK"
        headers: list[tuple[str, str]] = []
        for line in head.decode("latin-1").split("\r\n"):
            key, _, value = line.partition(": ")
            if key.lower() == "status":
                status = value
            elif key:
                headers.append((key, value))
        start_response(status, headers)
        return [payload]

    return app


@contextmanager
def serve_git(root: Path) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """Serve bare repositories under ``root``; yields (base_url, request_headers_log)."""
    seen: list[dict[str, str]] = []
    server = make_server(
        "127.0.0.1", 0, _app(root, seen), server_class=_ThreadingServer, handler_class=_QuietHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", seen
    finally:
        server.shutdown()
        server.server_close()


def make_bare_repo(
    root: Path, name: str, files: dict[str, bytes | str], *, symlinks: dict[str, str] | None = None
) -> Path:
    """Create ``root/name.git`` with one commit containing ``files`` (and optional symlinks)."""
    work = root / f"{name}-work"
    work.mkdir(parents=True)
    for rel, content in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    for rel, target in (symlinks or {}).items():
        (work / rel).symlink_to(target)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=work, check=True, env=env)
    subprocess.run([*git, "add", "-A"], cwd=work, check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=work, check=True, env=env)
    bare = root / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, env=env)
    subprocess.run(
        ["git", "config", "uploadpack.allowReachableSHA1InWant", "true"],
        cwd=bare,
        check=True,
        env=env,
    )
    return bare
