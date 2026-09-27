"""A small typed GitHub REST client over httpx.

Only the endpoints DevAgent needs: the signed-in user, repositories, issues, and the
Git Data API for building a commit without a local clone. Tokens are held as
``SecretStr`` and only ever placed in the ``Authorization`` header.
"""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import quote

import httpx
from pydantic import SecretStr

API_VERSION: Final = "2022-11-28"
USER_AGENT: Final = "DevAgent"


class GitHubError(Exception):
    """A GitHub API call failed. ``code`` is stable and safe to show to users."""

    code = "github_error"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class GitHubNotFoundError(GitHubError):
    code = "github_not_found"


class GitHubPermissionError(GitHubError):
    code = "github_permission_denied"


class GitHubAuthError(GitHubError):
    code = "github_unauthorized"


class GitHubRateLimitError(GitHubError):
    code = "github_rate_limited"

    def __init__(self, message: str, *, status: int | None, reset_at: datetime | None) -> None:
        super().__init__(message, status=status)
        self.reset_at = reset_at


class GitHubConflictError(GitHubError):
    code = "github_conflict"


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str
    default_branch: str
    private: bool
    can_push: bool
    html_url: str


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    body: str
    html_url: str
    labels: list[str]
    comments: int
    user: str | None
    created_at: str


@dataclass(frozen=True)
class CreatedPull:
    number: int
    html_url: str
    head_sha: str
    draft: bool


def _reset_time(response: httpx.Response) -> datetime | None:
    value = response.headers.get("x-ratelimit-reset")
    if value and value.isdigit():
        return datetime.fromtimestamp(int(value), tz=UTC)
    return None


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return str(body["message"])
    return f"HTTP {response.status_code}"


def raise_for_github(response: httpx.Response) -> None:
    """Map an error response to a typed ``GitHubError``."""
    if response.is_success:
        return
    status = response.status_code
    message = _message(response)
    rate_limited = response.headers.get("x-ratelimit-remaining") == "0" or (
        "rate limit" in message.lower()
    )
    if status == HTTPStatus.TOO_MANY_REQUESTS or (status == HTTPStatus.FORBIDDEN and rate_limited):
        raise GitHubRateLimitError(
            f"GitHub rate limit reached: {message}", status=status, reset_at=_reset_time(response)
        )
    if status == HTTPStatus.UNAUTHORIZED:
        raise GitHubAuthError(f"GitHub rejected the token: {message}", status=status)
    if status == HTTPStatus.FORBIDDEN:
        raise GitHubPermissionError(f"GitHub denied access: {message}", status=status)
    if status == HTTPStatus.NOT_FOUND:
        raise GitHubNotFoundError(f"Not found on GitHub: {message}", status=status)
    if status in {HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY}:
        raise GitHubConflictError(message, status=status)
    raise GitHubError(f"GitHub API error: {message}", status=status)


class GitHubClient:
    """Async GitHub REST client. One instance per token; close it with ``aclose``."""

    def __init__(
        self,
        token: SecretStr | None,
        *,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )
        self.authenticated = token is not None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub: {type(exc).__name__}") from exc
        raise_for_github(response)
        return response

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401 - GitHub JSON, narrowed by each caller
        return (await self._request(method, path, params=params, json=json)).json()

    @staticmethod
    def _repo_path(owner: str, name: str) -> str:
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    # ------------------------------------------------------------------ reads

    async def get_user(self) -> GitHubUser:
        data = await self._json("GET", "/user")
        return GitHubUser(
            id=int(data["id"]),
            login=str(data["login"]),
            name=data.get("name"),
            email=data.get("email"),
            avatar_url=data.get("avatar_url"),
        )

    async def get_repo(self, owner: str, name: str) -> GitHubRepo:
        data = await self._json("GET", self._repo_path(owner, name))
        permissions = data.get("permissions") or {}
        return GitHubRepo(
            owner=str(data["owner"]["login"]),
            name=str(data["name"]),
            default_branch=str(data["default_branch"]),
            private=bool(data.get("private", False)),
            can_push=bool(permissions.get("push", False)),
            html_url=str(data["html_url"]),
        )

    async def list_open_issues(self, owner: str, name: str, limit: int = 50) -> list[GitHubIssue]:
        data = await self._json(
            "GET",
            f"{self._repo_path(owner, name)}/issues",
            params={"state": "open", "per_page": min(limit, 100), "sort": "updated"},
        )
        issues: list[GitHubIssue] = []
        for item in data:
            if "pull_request" in item:  # the issues endpoint also lists pull requests
                continue
            issues.append(
                GitHubIssue(
                    number=int(item["number"]),
                    title=str(item["title"]),
                    body=str(item.get("body") or ""),
                    html_url=str(item["html_url"]),
                    labels=[str(label["name"]) for label in item.get("labels", [])],
                    comments=int(item.get("comments", 0)),
                    user=(item.get("user") or {}).get("login"),
                    created_at=str(item.get("created_at", "")),
                )
            )
        return issues[:limit]

    async def branch_head(self, owner: str, name: str, branch: str) -> str:
        data = await self._json(
            "GET", f"{self._repo_path(owner, name)}/git/ref/heads/{quote(branch, safe='/')}"
        )
        return str(data["object"]["sha"])

    async def changed_files_between(self, owner: str, name: str, base: str, head: str) -> set[str]:
        """Paths changed between two commits (the compare API lists up to 300 files)."""
        data = await self._json(
            "GET",
            f"{self._repo_path(owner, name)}/compare/{base}...{head}",
            params={"per_page": 100},
        )
        paths: set[str] = set()
        for entry in data.get("files", []):
            paths.add(str(entry["filename"]))
            if entry.get("previous_filename"):
                paths.add(str(entry["previous_filename"]))
        return paths

    async def file_at(self, owner: str, name: str, path: str, ref: str) -> bytes | None:
        """A file's bytes at a commit, or None if it does not exist there."""
        try:
            data = await self._json(
                "GET",
                f"{self._repo_path(owner, name)}/contents/{quote(path)}",
                params={"ref": ref},
            )
        except GitHubNotFoundError:
            return None
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubError(f"{path} is not a regular file at {ref[:12]}")
        if data.get("encoding") == "base64" and data.get("content") is not None:
            return base64.b64decode(str(data["content"]))
        # Files over 1 MB come without inline content; fetch the blob instead.
        blob = await self._json("GET", f"{self._repo_path(owner, name)}/git/blobs/{data['sha']}")
        return base64.b64decode(str(blob["content"]))

    async def commit_tree(self, owner: str, name: str, commit_sha: str) -> str:
        data = await self._json("GET", f"{self._repo_path(owner, name)}/git/commits/{commit_sha}")
        return str(data["tree"]["sha"])

    async def file_modes(self, owner: str, name: str, commit_sha: str) -> dict[str, str]:
        """``path -> mode`` for every blob at a commit (recursive tree listing)."""
        tree = await self.commit_tree(owner, name, commit_sha)
        data = await self._json(
            "GET", f"{self._repo_path(owner, name)}/git/trees/{tree}", params={"recursive": "1"}
        )
        if data.get("truncated"):
            raise GitHubError("the repository tree is too large to list")
        return {
            str(entry["path"]): str(entry["mode"])
            for entry in data.get("tree", [])
            if entry.get("type") == "blob"
        }

    async def ref_exists(self, owner: str, name: str, branch: str) -> bool:
        try:
            await self.branch_head(owner, name, branch)
        except GitHubNotFoundError:
            return False
        return True

    # ------------------------------------------------------------------ writes (backend only)

    async def create_blob(self, owner: str, name: str, content: bytes) -> str:
        data = await self._json(
            "POST",
            f"{self._repo_path(owner, name)}/git/blobs",
            json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
        )
        return str(data["sha"])

    async def create_tree(
        self, owner: str, name: str, base_tree: str, entries: list[dict[str, Any]]
    ) -> str:
        data = await self._json(
            "POST",
            f"{self._repo_path(owner, name)}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
        )
        return str(data["sha"])

    async def create_commit(
        self,
        owner: str,
        name: str,
        *,
        message: str,
        tree: str,
        parent: str,
        author: dict[str, str],
    ) -> str:
        data = await self._json(
            "POST",
            f"{self._repo_path(owner, name)}/git/commits",
            json={
                "message": message,
                "tree": tree,
                "parents": [parent],
                "author": author,
                "committer": author,
            },
        )
        return str(data["sha"])

    async def create_branch(self, owner: str, name: str, branch: str, sha: str) -> None:
        await self._json(
            "POST",
            f"{self._repo_path(owner, name)}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def create_draft_pull(
        self, owner: str, name: str, *, title: str, body: str, head: str, base: str
    ) -> CreatedPull:
        data = await self._json(
            "POST",
            f"{self._repo_path(owner, name)}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": True,
                "maintainer_can_modify": True,
            },
        )
        return CreatedPull(
            number=int(data["number"]),
            html_url=str(data["html_url"]),
            head_sha=str(data["head"]["sha"]),
            draft=bool(data.get("draft", True)),
        )
