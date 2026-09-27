"""Opening a draft PR from an approved diff, against an in-memory GitHub."""

import hashlib
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from backend.github.client import GitHubClient, GitHubPermissionError
from backend.github.publisher import (
    BaseMovedError,
    PatchApplyError,
    PullRequestPublisher,
    PullRequestSpec,
    StaleApprovalError,
    branch_name,
)
from backend.logging_setup import configure_logging
from tests.github_fake import FakeGitHub, FakeRepo, Tree

TOKEN = "ghp_" + "T0k3n" * 8
SLUG = Path(__file__).parents[2] / "sample_repos" / "slugger" / "slugger" / "slug.py"
BUGGY = SLUG.read_bytes()
FIXED = BUGGY.replace(
    b"    if words[0].isdigit():",
    b'    if not words:\n        return ""\n    if words[0].isdigit():',
)
NEW_TEST = b'from slugger import slugify\n\n\ndef test_empty():\n    assert slugify("") == ""\n'


def git_diff(tmp: Path, before: Tree, after: Tree) -> str:
    """A real ``git diff --cached -M`` between two trees, like the agent produces."""
    repo = tmp / "diffrepo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    for tree in (before, after):
        for existing in list(repo.rglob("*")):
            if existing.is_file() and ".git" not in existing.parts:
                existing.unlink()
        for path, (mode, content) in tree.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o755 if mode == "100755" else 0o644)
        git("add", "-A")
        if tree is before:
            git("commit", "-q", "-m", "base")
    return git("diff", "--cached", "--no-color", "-M", "HEAD")


BASE: Tree = {
    "slugger/slug.py": ("100644", BUGGY),
    "slugger/__init__.py": ("100644", b"from slugger.slug import slugify\n"),
    "scripts/run.sh": ("100755", b"#!/bin/sh\necho old\n"),
    "README.md": ("100644", b"# slugger\n"),
}
AFTER: Tree = {
    **BASE,
    "slugger/slug.py": ("100644", FIXED),
    "tests/test_slug_empty.py": ("100644", NEW_TEST),
}


@pytest.fixture
def fake() -> FakeGitHub:
    fake = FakeGitHub(token=TOKEN)
    repo = fake.add_repo(FakeRepo("octo", "slugger"))
    repo.refs["main"] = repo.commit(BASE)
    return fake


def spec(fake: FakeGitHub, diff: str, **overrides: object) -> PullRequestSpec:
    repo = fake.repos["octo/slugger"]
    values: dict[str, object] = {
        "owner": "octo",
        "repo": "slugger",
        "base_commit": repo.refs["main"],
        "diff": diff,
        "approved_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "title": "Return an empty slug for titles without letters",
        "body": "## Issue\n\nFixes #3.",
        "commit_message": "Return an empty slug for titles without letters\n\nFixes #3.",
        "issue_number": 3,
        "issue_title": "slugify crashes on titles without letters",
        "run_id": "0f0e0d0c-0000-0000-0000-000000000000",
        "author_name": "DevAgent",
        "author_email": "devagent@users.noreply.github.com",
    }
    values.update(overrides)
    return PullRequestSpec(**values)  # type: ignore[arg-type]


def client(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient(
        SecretStr(TOKEN), base_url="https://api.github.test", transport=fake.transport()
    )


async def test_opens_a_draft_pr_with_exactly_the_approved_change(
    tmp_path: Path, fake: FakeGitHub
) -> None:
    diff = git_diff(tmp_path, BASE, AFTER)
    async with client(fake) as gh:
        pull = await PullRequestPublisher(gh).publish(spec(fake, diff))

    repo = fake.repos["octo/slugger"]
    assert pull.branch == "devagent/issue-3-slugify-crashes-on-titles-without"
    assert pull.url == "https://github.com/octo/slugger/pull/1"
    assert not pull.base_moved
    tree, parent, message = repo.commits[repo.refs[pull.branch]]
    assert parent == repo.refs["main"]  # built on the validated base commit
    assert tree == AFTER  # the fix, the new test, nothing else, modes kept
    assert message.startswith("Return an empty slug")
    assert repo.pulls[0]["draft"] is True
    assert repo.pulls[0]["base"] == "main"
    assert repo.pulls[0]["body"] == "## Issue\n\nFixes #3."
    assert fake.last_commit["author"] == {
        "name": "DevAgent",
        "email": "devagent@users.noreply.github.com",
    }


async def test_deletions_renames_and_mode_changes(tmp_path: Path, fake: FakeGitHub) -> None:
    after: Tree = {k: v for k, v in BASE.items() if k != "README.md"}
    after["scripts/run.sh"] = ("100644", b"#!/bin/sh\necho new\n")
    after["slugger/core.py"] = after.pop("slugger/__init__.py")
    diff = git_diff(tmp_path, BASE, after)
    assert "rename from slugger/__init__.py" in diff
    async with client(fake) as gh:
        pull = await PullRequestPublisher(gh).publish(spec(fake, diff))
    repo = fake.repos["octo/slugger"]
    assert repo.commits[repo.refs[pull.branch]][0] == after


async def test_approval_is_bound_to_the_diff_hash(tmp_path: Path, fake: FakeGitHub) -> None:
    diff = git_diff(tmp_path, BASE, AFTER)
    async with client(fake) as gh:
        with pytest.raises(StaleApprovalError):
            await PullRequestPublisher(gh).publish(spec(fake, diff, approved_sha256="0" * 64))
    assert fake.requests == []  # nothing was sent to GitHub


async def test_no_push_permission(tmp_path: Path, fake: FakeGitHub) -> None:
    fake.repos["octo/slugger"].can_push = False
    async with client(fake) as gh:
        with pytest.raises(GitHubPermissionError):
            await PullRequestPublisher(gh).publish(spec(fake, git_diff(tmp_path, BASE, AFTER)))
    assert all(r.method == "GET" for r in fake.requests)


async def test_base_branch_moved(tmp_path: Path, fake: FakeGitHub) -> None:
    repo = fake.repos["octo/slugger"]
    base = repo.refs["main"]
    diff = git_diff(tmp_path, BASE, AFTER)

    # Upstream changed an unrelated file: the PR is based on the validated commit.
    repo.refs["main"] = repo.commit({**BASE, "README.md": ("100644", b"# slugger!\n")}, base)
    async with client(fake) as gh:
        pull = await PullRequestPublisher(gh).publish(spec(fake, diff, base_commit=base))
    assert pull.base_moved
    assert repo.commits[repo.refs[pull.branch]][1] == base
    assert "has moved since" in repo.pulls[0]["body"]

    # Upstream changed a file the fix touches: ask for a new run.
    repo.refs["main"] = repo.commit({**BASE, "slugger/slug.py": ("100644", BUGGY + b"#\n")}, base)
    async with client(fake) as gh:
        with pytest.raises(BaseMovedError, match=r"slugger/slug\.py"):
            await PullRequestPublisher(gh).publish(spec(fake, diff, base_commit=base))


async def test_branch_name_collisions_get_a_suffix(tmp_path: Path, fake: FakeGitHub) -> None:
    repo = fake.repos["octo/slugger"]
    s = spec(fake, git_diff(tmp_path, BASE, AFTER))
    repo.refs[branch_name(s)] = repo.refs["main"]
    repo.refs[branch_name(s, 2)] = repo.refs["main"]
    async with client(fake) as gh:
        pull = await PullRequestPublisher(gh).publish(s)
    assert pull.branch == branch_name(s, 3)
    assert branch_name(spec(fake, "", issue_number=None)).startswith("devagent/run-0f0e0d0c-")


async def test_a_diff_that_does_not_apply_is_refused(tmp_path: Path, fake: FakeGitHub) -> None:
    other = {**BASE, "slugger/slug.py": ("100644", b"something else entirely\n")}
    diff = git_diff(tmp_path, other, {**other, "slugger/slug.py": ("100644", b"changed\n")})
    async with client(fake) as gh:
        with pytest.raises(PatchApplyError, match="does not apply"):
            await PullRequestPublisher(gh).publish(spec(fake, diff))
    assert not fake.repos["octo/slugger"].pulls


async def test_the_token_is_only_sent_in_the_authorization_header(
    tmp_path: Path, fake: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("DEBUG", "json")
    async with client(fake) as gh:
        await PullRequestPublisher(gh).publish(spec(fake, git_diff(tmp_path, BASE, AFTER)))
    assert fake.requests
    for request in fake.requests:
        assert request.authorization == f"Bearer {TOKEN}"
        assert TOKEN not in request.path
        assert TOKEN not in request.body
    repo = fake.repos["octo/slugger"]
    assert all(
        TOKEN.encode() not in content
        for _, content in repo.commits[repo.refs[repo.pulls[0]["head"]]][0].values()
    )
    out = capsys.readouterr()
    assert "pull_request_opened" in out.out + out.err
    assert TOKEN not in out.out + out.err
