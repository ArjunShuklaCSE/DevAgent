"""An in-memory GitHub REST API for tests (served through ``httpx.MockTransport``).

It implements the endpoints DevAgent calls, with real git semantics where they matter:
commits have trees of ``path -> (mode, bytes)``, refs point at commits, trees can be
built on a base tree with deletions, and pull requests record their head and base.
"""

import base64
import hashlib
import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

Tree = dict[str, tuple[str, bytes]]


@dataclass
class FakeRepo:
    owner: str
    name: str
    default_branch: str = "main"
    can_push: bool = True
    private: bool = False
    commits: dict[str, tuple[Tree, str | None, str]] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    blobs: dict[str, bytes] = field(default_factory=dict)
    trees: dict[str, Tree] = field(default_factory=dict)
    pulls: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def commit(self, files: Tree, parent: str | None = None, message: str = "c") -> str:
        sha = hashlib.sha1(  # noqa: S324 - fake object ids
            json.dumps(
                [sorted((p, m, c.hex()) for p, (m, c) in files.items()), parent, message]
            ).encode()
        ).hexdigest()
        self.commits[sha] = (dict(files), parent, message)
        self.trees[f"t{sha}"] = dict(files)
        return sha


@dataclass
class Recorded:
    method: str
    path: str
    authorization: str | None
    body: str


class FakeGitHub:
    def __init__(self, token: str | None = None) -> None:
        self.repos: dict[str, FakeRepo] = {}
        self.requests: list[Recorded] = []
        self.token = token  # when set, requests with another token get 401
        self.user = {
            "id": 4242,
            "login": "octo",
            "name": "Octo Cat",
            "email": None,
            "avatar_url": None,
        }
        self.rate_limited = False
        self._ids = itertools.count(1)
        self.oauth_codes: dict[str, str] = {}

    def add_repo(self, repo: FakeRepo) -> FakeRepo:
        self.repos[f"{repo.owner}/{repo.name}"] = repo
        return repo

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ------------------------------------------------------------------ routing

    def _handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911, PLR0912, PLR0915 - one flat router
        path = unquote(request.url.path)
        body = request.content.decode() if request.content else ""
        self.requests.append(
            Recorded(request.method, str(request.url), request.headers.get("authorization"), body)
        )
        if path == "/login/oauth/access_token":
            return self._oauth(body)
        if self.rate_limited:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"},
            )
        auth = request.headers.get("authorization")
        if self.token is not None and auth is not None and auth != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if path == "/user":
            return httpx.Response(200, json=self.user)
        match = re.match(r"^/repos/([^/]+)/([^/]+)(/.*)?$", path)
        if not match:
            return httpx.Response(404, json={"message": "Not Found"})
        repo = self.repos.get(f"{match[1]}/{match[2]}")
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        rest = match[3] or ""
        data = json.loads(body) if body else {}
        params = request.url.params

        if rest == "" and request.method == "GET":
            return httpx.Response(200, json=self._repo_json(repo))
        if rest == "/issues":
            return httpx.Response(200, json=repo.issues)
        if m := re.match(r"^/git/ref/heads/(.+)$", rest):
            sha = repo.refs.get(m[1])
            if sha is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"ref": f"refs/heads/{m[1]}", "object": {"sha": sha}})
        if m := re.match(r"^/compare/([0-9a-f]+)\.\.\.([0-9a-f]+)$", rest):
            base, head = repo.commits[m[1]][0], repo.commits[m[2]][0]
            changed = sorted(p for p in set(base) | set(head) if base.get(p) != head.get(p))
            return httpx.Response(200, json={"files": [{"filename": p} for p in changed]})
        if m := re.match(r"^/contents/(.+)$", rest):
            files = repo.commits[params["ref"]][0]
            if m[1] not in files:
                return httpx.Response(404, json={"message": "Not Found"})
            content = files[m[1]][1]
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode(),
                    "sha": "x",
                },
            )
        if m := re.match(r"^/git/commits/([0-9a-f]+)$", rest):
            if m[1] not in repo.commits:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"sha": m[1], "tree": {"sha": f"t{m[1]}"}})
        if m := re.match(r"^/git/trees/(t[0-9a-f]+)$", rest):
            tree = repo.trees[m[1]]
            entries = [{"path": p, "mode": mode, "type": "blob"} for p, (mode, _) in tree.items()]
            return httpx.Response(200, json={"tree": entries, "truncated": False})
        if rest == "/git/blobs" and request.method == "POST":
            content = base64.b64decode(data["content"])
            sha = hashlib.sha1(content).hexdigest()  # noqa: S324
            repo.blobs[sha] = content
            return httpx.Response(201, json={"sha": sha})
        if not repo.can_push and request.method == "POST":
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        if rest == "/git/trees" and request.method == "POST":
            tree = dict(repo.trees[data["base_tree"]])
            for entry in data["tree"]:
                if entry["sha"] is None:
                    tree.pop(entry["path"], None)
                else:
                    tree[entry["path"]] = (entry["mode"], repo.blobs[entry["sha"]])
            sha = f"t{next(self._ids):039x}"
            repo.trees[sha] = tree
            return httpx.Response(201, json={"sha": sha})
        if rest == "/git/commits" and request.method == "POST":
            sha = repo.commit(repo.trees[data["tree"]], data["parents"][0], data["message"])
            repo.commits[sha] = (repo.trees[data["tree"]], data["parents"][0], data["message"])
            self.last_commit = data
            return httpx.Response(201, json={"sha": sha})
        if rest == "/git/refs" and request.method == "POST":
            branch = data["ref"].removeprefix("refs/heads/")
            if branch in repo.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            repo.refs[branch] = data["sha"]
            return httpx.Response(201, json={"ref": data["ref"]})
        if rest == "/pulls" and request.method == "POST":
            number = len(repo.pulls) + 1
            pull = {**data, "number": number, "head_sha": repo.refs[data["head"]]}
            repo.pulls.append(pull)
            return httpx.Response(
                201,
                json={
                    "number": number,
                    "html_url": f"https://github.com/{repo.owner}/{repo.name}/pull/{number}",
                    "head": {"sha": pull["head_sha"]},
                    "draft": data["draft"],
                },
            )
        return httpx.Response(
            404, json={"message": f"fake has no route for {request.method} {rest}"}
        )

    def _repo_json(self, repo: FakeRepo) -> dict[str, Any]:
        return {
            "name": repo.name,
            "owner": {"login": repo.owner},
            "default_branch": repo.default_branch,
            "private": repo.private,
            "permissions": {"push": repo.can_push, "pull": True},
            "html_url": f"https://github.com/{repo.owner}/{repo.name}",
        }

    def _oauth(self, body: str) -> httpx.Response:
        form = dict(pair.split("=", 1) for pair in body.split("&") if "=" in pair)
        token = self.oauth_codes.pop(unquote(form.get("code", "")), None)
        if token is None:
            return httpx.Response(200, json={"error": "bad_verification_code"})
        return httpx.Response(
            200, json={"access_token": token, "scope": "read:user,repo", "token_type": "bearer"}
        )
