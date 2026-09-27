"""GitHub client error mapping, issue import, OAuth, patch files and token encryption."""

import subprocess
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from backend.crypto import CryptoError, SecretBox, generate_key
from backend.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubConflictError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    raise_for_github,
)
from backend.github.oauth import GitHubOAuth
from backend.github.patch import PatchError, parse_diff, render_patch
from tests.github_fake import FakeGitHub, FakeRepo


def response(status: int, message: str = "x", **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"message": message}, headers=headers)


@pytest.mark.parametrize(
    ("resp", "error"),
    [
        (response(401, "Bad credentials"), GitHubAuthError),
        (response(403, "Resource not accessible"), GitHubPermissionError),
        (
            response(403, "API rate limit exceeded", **{"x-ratelimit-remaining": "0"}),
            GitHubRateLimitError,
        ),
        (response(429, "secondary rate limit"), GitHubRateLimitError),
        (response(404, "Not Found"), GitHubNotFoundError),
        (response(422, "Reference already exists"), GitHubConflictError),
    ],
)
def test_errors_are_typed(resp: httpx.Response, error: type[Exception]) -> None:
    with pytest.raises(error):
        raise_for_github(resp)


def test_rate_limit_carries_the_reset_time() -> None:
    with pytest.raises(GitHubRateLimitError) as info:
        raise_for_github(
            response(
                403,
                "rate limit",
                **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"},
            )
        )
    assert info.value.reset_at is not None
    assert info.value.reset_at.year == 2026


async def test_issue_import_skips_pull_requests() -> None:
    fake = FakeGitHub()
    repo = fake.add_repo(FakeRepo("octo", "slugger"))
    repo.issues = [
        {
            "number": 3,
            "title": "slugify crashes",
            "body": None,
            "html_url": "u3",
            "labels": [{"name": "bug"}],
            "comments": 2,
            "user": {"login": "a"},
            "created_at": "2026-09-01T00:00:00Z",
        },
        {
            "number": 4,
            "title": "a PR",
            "body": "",
            "html_url": "u4",
            "labels": [],
            "comments": 0,
            "user": None,
            "pull_request": {},
        },
    ]
    async with GitHubClient(
        None, base_url="https://api.github.test", transport=fake.transport()
    ) as gh:
        issues = await gh.list_open_issues("octo", "slugger")
        with pytest.raises(GitHubNotFoundError):
            await gh.list_open_issues("octo", "missing")
    assert [(i.number, i.body, i.labels) for i in issues] == [(3, "", ["bug"])]
    assert fake.requests[0].authorization is None  # anonymous access to public data


async def test_oauth_flow() -> None:
    fake = FakeGitHub()
    fake.oauth_codes["c0de"] = "gho_" + "a" * 36
    oauth = GitHubOAuth(
        "cid",
        SecretStr("csecret"),
        "http://web/api/v1/auth/github/callback",
        web_url="https://github.test",
        transport=fake.transport(),
    )
    url = oauth.authorize_url("st4te")
    assert url.startswith("https://github.test/login/oauth/authorize?client_id=cid")
    assert "state=st4te" in url
    assert "scope=read%3Auser+repo" in url
    token = await oauth.exchange("c0de")
    assert token.access_token.get_secret_value() == "gho_" + "a" * 36
    assert token.scopes == ["read:user", "repo"]
    with pytest.raises(GitHubAuthError, match="bad_verification_code"):
        await oauth.exchange("c0de")  # codes are single-use


def test_tokens_are_encrypted_at_rest() -> None:
    box = SecretBox(SecretStr(generate_key()))
    token = SecretStr("ghp_" + "s" * 36)
    ciphertext = box.encrypt_token(token)
    assert b"ghp_" not in ciphertext
    assert box.decrypt_token(ciphertext).get_secret_value() == token.get_secret_value()
    with pytest.raises(CryptoError):
        SecretBox(SecretStr(generate_key())).decrypt_token(ciphertext)  # another key
    with pytest.raises(CryptoError, match="Fernet key"):
        SecretBox(SecretStr("not-a-key"))


def test_sealed_cookies_are_purpose_bound_and_expire() -> None:
    box = SecretBox(SecretStr(generate_key()))
    sealed = box.seal("session", {"user_id": "u"})
    assert box.unseal("session", sealed, 60) == {"user_id": "u"}
    with pytest.raises(CryptoError, match="another purpose"):
        box.unseal("oauth_state", sealed, 60)
    with pytest.raises(CryptoError):
        box.unseal("session", sealed[:-2] + "xx", 60)
    old = box._fernet.encrypt_at_time(b'{"p": "session", "d": {}}', int(time.time()) - 120).decode()
    with pytest.raises(CryptoError, match="expired"):
        box.unseal("session", old, 60)


def test_diff_parser_rejects_unsafe_paths() -> None:
    with pytest.raises(PatchError):
        parse_diff("diff --git a/../etc/passwd b/../etc/passwd\n")
    with pytest.raises(PatchError):
        parse_diff("diff --git a/.git/config b/.git/config\n")
    assert parse_diff("") == []


def test_patch_file_applies_with_git_am(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path = tmp_path) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "t.py").write_text("def test(): pass\n")
    git("add", "-A")
    diff = git("diff", "--cached", "HEAD")
    git("reset", "-q", "--hard")

    patch = render_patch(
        diff,
        subject="Fix x",
        body="Explain.\n\nFixes #3.",
        author_name="DevAgent",
        author_email="d@e",
    )
    (tmp_path / "fix.patch").write_text(patch)
    git("am", "-q", "fix.patch")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    log = git("log", "-1", "--format=%an <%ae>%n%s%n%b")
    assert log.startswith("DevAgent <d@e>\nFix x\nExplain.")
    assert "Fixes #3." in log
