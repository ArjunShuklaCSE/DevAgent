"""Clone safety (spec 6.4) against a real smart-HTTP git server."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.git_server import make_bare_repo, serve_git
from workspace.clone import CloneConfig, CloneError, GitCloner, copy_local_repository
from workspace.limits import RepoLimits

pytestmark = pytest.mark.security

Server = tuple[Path, str, list[dict[str, str]]]


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    root = tmp_path / "srv"
    root.mkdir()
    with serve_git(root) as (url, seen):
        yield root, url, seen


def _cloner(url: str, **limits: int) -> GitCloner:
    return GitCloner(
        CloneConfig(
            allowed_url_prefixes=(url,),
            limits=RepoLimits(**limits) if limits else RepoLimits(),
            timeout_seconds=30,
            size_poll_seconds=0.05,
        )
    )


async def test_clones_single_commit_and_reports_sha(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "ok", {"pkg/a.py": "x = 1\n", "README.md": "hi"})
    result = await _cloner(url).clone(f"{url}ok.git", tmp_path / "ws")
    assert (result.path / "pkg/a.py").read_text() == "x = 1\n"
    assert len(result.commit_sha) == 40
    assert result.stats.files >= 2


async def test_rejects_urls_outside_allowlist(tmp_path: Path) -> None:
    cloner = GitCloner()  # default: https://github.com/ only
    for url in [
        "http://github.com/o/r",
        "file:///etc",
        "ext::sh -c touch% /tmp/pwned",
        "https://github.com.evil.example/o/r",
        "https://user:pass@github.com/o/r",
        "git@github.com:o/r.git",
    ]:
        with pytest.raises(CloneError) as info:
            await cloner.clone(url, tmp_path / "ws")
        assert info.value.code == "url_not_allowed"


async def test_rejects_option_injection_in_ref(server: Server, tmp_path: Path) -> None:
    _, url, _ = server
    for ref in ["--upload-pack=touch /tmp/x", "-oops", "main..HEAD", "a b"]:
        with pytest.raises(CloneError) as info:
            await _cloner(url).clone(f"{url}r.git", tmp_path / "ws", ref=ref)
        assert info.value.code == "invalid_ref"


async def test_hooks_from_host_config_never_run(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, url, _ = server
    make_bare_repo(root, "hooks", {"a.txt": "a"})
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / "evil-hooks"
    hooks.mkdir()
    for name in ("post-checkout", "post-merge", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
    evil_home = tmp_path / "home"
    evil_home.mkdir()
    (evil_home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("HOME", str(evil_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(evil_home / ".gitconfig"))

    await _cloner(url).clone(f"{url}hooks.git", tmp_path / "ws")

    assert not marker.exists()
    hooks_dir = tmp_path / "ws/.git/hooks"
    assert not hooks_dir.exists() or not any(hooks_dir.iterdir())


async def test_symlinks_are_checked_out_as_plain_files(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "links", {"a.txt": "a"}, symlinks={"escape": "/etc/passwd"})
    result = await _cloner(url).clone(f"{url}links.git", tmp_path / "ws")
    escape = result.path / "escape"
    assert not escape.is_symlink()
    assert escape.read_text() == "/etc/passwd"


async def test_submodules_are_not_fetched(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "sub", {"x.txt": "x"})
    gitmodules = f'[submodule "sub"]\n\tpath = vendor/sub\n\turl = {url}sub.git\n'
    make_bare_repo(root, "parent", {".gitmodules": gitmodules, "vendor/sub/.keep": ""})
    result = await _cloner(url).clone(f"{url}parent.git", tmp_path / "ws")
    assert not (result.path / "vendor/sub/x.txt").exists()


async def test_oversized_repository_is_rejected_and_removed(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "big", {f"f{i}.txt": "x" for i in range(50)})
    dest = tmp_path / "ws"
    with pytest.raises(CloneError) as info:
        await _cloner(url, max_files=20).clone(f"{url}big.git", dest)
    assert info.value.code == "repository_too_large"
    assert "20 files" in str(info.value)
    assert not dest.exists()


async def test_oversized_download_is_stopped(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "blob", {"big.bin": os.urandom(3 * 1024 * 1024)})
    with pytest.raises(CloneError) as info:
        await _cloner(url, max_bytes=1024 * 1024).clone(f"{url}blob.git", tmp_path / "ws")
    assert info.value.code == "repository_too_large"


async def test_token_is_sent_but_never_written_to_the_workspace(
    server: Server, tmp_path: Path
) -> None:
    root, url, seen = server
    make_bare_repo(root, "priv", {"a.txt": "a"})
    token = "ghs_" + "T" * 36
    result = await _cloner(url).clone(f"{url}priv.git", tmp_path / "ws", token=token)

    assert any("HTTP_AUTHORIZATION" in headers for headers in seen)
    for path in result.path.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert token.encode() not in data, path
            assert b"extraheader" not in data, path


async def test_unknown_ref_fails_cleanly(server: Server, tmp_path: Path) -> None:
    root, url, _ = server
    make_bare_repo(root, "refs", {"a.txt": "a"})
    with pytest.raises(CloneError) as info:
        await _cloner(url).clone(f"{url}refs.git", tmp_path / "ws", ref="does-not-exist")
    assert info.value.code == "clone_failed"


async def test_copy_local_repository_commits_base(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "sample_repos" / "textchunk"
    result = await copy_local_repository(source, tmp_path / "ws", RepoLimits())
    assert len(result.commit_sha) == 40
    assert (result.path / "src/textchunk/chunking.py").exists()
    with pytest.raises(CloneError) as info:
        await copy_local_repository(source, tmp_path / "ws2", RepoLimits(max_files=2))
    assert info.value.code == "repository_too_large"
