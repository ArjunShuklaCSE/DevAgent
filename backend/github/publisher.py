"""Open a draft pull request for an approved diff (spec 2.8 and 8).

Runs in backend code after human approval, never from a model tool call. The token is
only used for GitHub API requests: the commit is built with the Git Data API from the
base commit's files and the approved diff, applied with ``git apply`` in a private
temporary directory. The token never touches the agent's workspace or the sandbox.
"""

import asyncio
import hashlib
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from backend.github.client import GitHubClient, GitHubError, GitHubPermissionError
from backend.github.patch import FileChange, PatchError, parse_diff

logger = structlog.get_logger(__name__)

MAX_BRANCH_ATTEMPTS = 20
GIT_APPLY_TIMEOUT_SECONDS = 60


class StaleApprovalError(GitHubError):
    code = "stale_diff"


class BaseMovedError(GitHubError):
    code = "base_moved"


class PatchApplyError(GitHubError):
    code = "patch_does_not_apply"


@dataclass(frozen=True)
class PullRequestSpec:
    owner: str
    repo: str
    base_commit: str
    diff: str
    approved_sha256: str
    title: str
    body: str
    commit_message: str
    issue_number: int | None
    issue_title: str
    run_id: str
    author_name: str
    author_email: str


@dataclass(frozen=True)
class PublishedPull:
    number: int
    url: str
    branch: str
    head_sha: str
    base_branch: str
    base_moved: bool


def slugify(text: str, max_length: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > max_length:
        cut = slug[: max_length + 1]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else slug[:max_length]
    return slug.strip("-") or "fix"


def branch_name(spec: PullRequestSpec, attempt: int = 1) -> str:
    stem = (
        f"devagent/issue-{spec.issue_number}-{slugify(spec.issue_title)}"
        if spec.issue_number is not None
        else f"devagent/run-{spec.run_id[:8]}-{slugify(spec.issue_title)}"
    )
    return stem if attempt == 1 else f"{stem}-{attempt}"


async def _git_apply(directory: Path, diff: str) -> None:
    for args in (["init", "-q"], ["apply", "--check", "-"], ["apply", "-"]):
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory)},
        )
        stdin = diff.encode() if args[0] == "apply" else None
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(stdin), timeout=GIT_APPLY_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            process.kill()
            raise PatchApplyError("git apply timed out") from exc
        if process.returncode != 0:
            raise PatchApplyError(
                f"the approved diff does not apply to the base commit: "
                f"{stderr.decode(errors='replace').strip()[:500]}"
            )


class PullRequestPublisher:
    def __init__(self, client: GitHubClient) -> None:
        self._gh = client

    async def publish(self, spec: PullRequestSpec) -> PublishedPull:
        digest = hashlib.sha256(spec.diff.encode()).hexdigest()
        if digest != spec.approved_sha256:
            raise StaleApprovalError("the diff does not match the approved hash; review it again")
        try:
            changes = parse_diff(spec.diff)
        except PatchError as exc:
            raise PatchApplyError(str(exc)) from exc
        if not changes:
            raise PatchApplyError("the approved diff is empty")
        if any(c.binary for c in changes):
            raise PatchApplyError("binary changes are not supported; download the patch instead")

        repo = await self._gh.get_repo(spec.owner, spec.repo)
        if not repo.can_push:
            raise GitHubPermissionError(
                f"the token cannot push to {spec.owner}/{spec.repo}", status=403
            )
        log = logger.bind(run_id=spec.run_id, repo=f"{spec.owner}/{spec.repo}")

        head = await self._gh.branch_head(spec.owner, spec.repo, repo.default_branch)
        base_moved = head != spec.base_commit
        if base_moved:
            upstream = await self._gh.changed_files_between(
                spec.owner, spec.repo, spec.base_commit, head
            )
            touched = {p for c in changes for p in (c.old_path, c.new_path) if p}
            overlap = sorted(touched & upstream)
            if overlap:
                raise BaseMovedError(
                    f"{repo.default_branch} has changed {', '.join(overlap)} since the run "
                    "started; start a new run on the current code"
                )

        entries = await self._tree_entries(spec, changes)
        base_tree = await self._gh.commit_tree(spec.owner, spec.repo, spec.base_commit)
        tree = await self._gh.create_tree(spec.owner, spec.repo, base_tree, entries)
        commit = await self._gh.create_commit(
            spec.owner,
            spec.repo,
            message=spec.commit_message,
            tree=tree,
            parent=spec.base_commit,
            author={"name": spec.author_name, "email": spec.author_email},
        )
        branch = await self._create_branch(spec, commit)
        body = spec.body
        if base_moved:
            body += (
                f"\n\n> This branch is based on `{spec.base_commit[:12]}`, the commit the fix "
                f"was validated on. `{repo.default_branch}` has moved since, without touching "
                "the changed files."
            )
        pull = await self._gh.create_draft_pull(
            spec.owner,
            spec.repo,
            title=spec.title,
            body=body,
            head=branch,
            base=repo.default_branch,
        )
        log.info("pull_request_opened", number=pull.number, branch=branch, base_moved=base_moved)
        return PublishedPull(
            number=pull.number,
            url=pull.html_url,
            branch=branch,
            head_sha=commit,
            base_branch=repo.default_branch,
            base_moved=base_moved,
        )

    async def _tree_entries(
        self, spec: PullRequestSpec, changes: list[FileChange]
    ) -> list[dict[str, Any]]:
        # Keep an existing file's mode (e.g. executable scripts) unless the diff changes it.
        modes = (
            await self._gh.file_modes(spec.owner, spec.repo, spec.base_commit)
            if any(c.old_path and not c.new_mode for c in changes)
            else {}
        )
        with tempfile.TemporaryDirectory(prefix="devagent-pr-") as tmp:
            root = Path(tmp) / "tree"
            root.mkdir()
            for change in changes:
                if change.old_path is None:
                    continue
                content = await self._gh.file_at(
                    spec.owner, spec.repo, change.old_path, spec.base_commit
                )
                if content is None:
                    raise PatchApplyError(
                        f"{change.old_path} does not exist at {spec.base_commit[:12]}"
                    )
                target = root / change.old_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            await _git_apply(root, spec.diff)

            entries: list[dict[str, Any]] = []
            for change in changes:
                if change.kind in {"delete", "rename"} and change.old_path:
                    entries.append(
                        {
                            "path": change.old_path,
                            "mode": modes.get(change.old_path, "100644"),
                            "type": "blob",
                            "sha": None,
                        }
                    )
                if change.new_path is None:
                    continue
                data = (root / change.new_path).read_bytes()
                blob = await self._gh.create_blob(spec.owner, spec.repo, data)
                entries.append(
                    {
                        "path": change.new_path,
                        "mode": change.new_mode or modes.get(change.old_path or "", "100644"),
                        "type": "blob",
                        "sha": blob,
                    }
                )
            return entries

    async def _create_branch(self, spec: PullRequestSpec, commit: str) -> str:
        for attempt in range(1, MAX_BRANCH_ATTEMPTS + 1):
            name = branch_name(spec, attempt)
            if await self._gh.ref_exists(spec.owner, spec.repo, name):
                continue
            await self._gh.create_branch(spec.owner, spec.repo, name, commit)
            return name
        raise GitHubError(f"{MAX_BRANCH_ATTEMPTS} branch names are already taken for this issue")
