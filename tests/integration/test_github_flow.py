"""Sign-in, approval and draft PR creation through the API and a real database.

GitHub is the in-memory fake from ``tests/github_fake.py``; everything else is real:
Postgres, the auth service, the publishing service and the worker job's code path.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import AppResources, create_app
from backend.config import Settings
from backend.crypto import generate_key
from backend.event_bus import InMemoryEventBus
from backend.github.client import GitHubClient
from backend.github.oauth import GitHubOAuth
from backend.services.auth import AuthService
from backend.services.publishing import publish_run
from core.run_status import RunStatus
from database.engine import create_engine, create_session_factory
from database.migrate import build_config
from database.models import (
    AgentRun,
    AgentStep,
    GithubCredential,
    Issue,
    IssueSource,
    PullRequest,
    Repository,
    RepositorySource,
    RunMode,
)
from tests.conftest import RecordingRunQueue
from tests.github_fake import FakeGitHub, FakeRepo
from tests.integration.db import temporary_database
from tests.unit.test_github_publisher import AFTER, BASE, git_diff

pytestmark = pytest.mark.integration

OAUTH_TOKEN = "gho_" + "Secr3t" * 6


@dataclass
class Harness:
    client: httpx.AsyncClient
    state: Any
    fake: FakeGitHub
    queue: RecordingRunQueue
    factory: async_sessionmaker[AsyncSession]
    settings: Settings

    @property
    def auth(self) -> AuthService:
        service: AuthService = self.state.auth_service
        return service

    async def sign_in(self) -> None:
        login = await self.client.get("/api/v1/auth/github/login", params={"next": "/runs"})
        assert login.status_code == 302
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        self.fake.oauth_codes["c0de"] = OAUTH_TOKEN
        callback = await self.client.get(
            "/api/v1/auth/github/callback", params={"code": "c0de", "state": state}
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == "http://web.test/runs"


@asynccontextmanager
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    fake = FakeGitHub(token=OAUTH_TOKEN)
    queue = RecordingRunQueue()
    settings = Settings(
        _env_file=None,
        environment="test",
        log_format="json",
        secret_key=SecretStr(generate_key()),
        github_client_id="cid",
        github_client_secret=SecretStr("csecret"),
        public_web_url="http://web.test",
        sample_repos_path=str(Path(__file__).parents[2] / "sample_repos"),
    )
    async with temporary_database() as url:
        await asyncio.to_thread(command.upgrade, build_config(url), "head")
        engine = create_engine(url)
        factory = create_session_factory(engine)

        @asynccontextmanager
        async def resources(_settings: Settings) -> AsyncIterator[AppResources]:
            yield AppResources(
                probes=[], session_factory=factory, event_bus=InMemoryEventBus(), run_queue=queue
            )

        app = create_app(
            settings,
            resources,
            github=lambda token: GitHubClient(
                token, base_url="https://api.github.test", transport=fake.transport()
            ),
            oauth=GitHubOAuth(
                "cid",
                SecretStr("csecret"),
                "http://web.test/api/v1/auth/github/callback",
                web_url="https://github.test",
                transport=fake.transport(),
            ),
        )
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    yield Harness(client, app.state, fake, queue, factory, settings)
        finally:
            await engine.dispose()


async def _awaiting_run(h: Harness, diff: str, base: str) -> AgentRun:
    async with h.factory() as session, session.begin():
        repo = Repository(
            source=RepositorySource.GITHUB,
            owner="octo",
            name="slugger",
            clone_url="https://github.com/octo/slugger.git",
        )
        session.add(repo)
        await session.flush()
        issue = Issue(
            repository_id=repo.id,
            source=IssueSource.GITHUB,
            number=3,
            title="slugify crashes on titles without letters",
            body="",
        )
        session.add(issue)
        await session.flush()
        run = AgentRun(
            repository_id=repo.id,
            issue_id=issue.id,
            mode=RunMode.AGENT,
            status=RunStatus.AWAITING_APPROVAL,
            base_commit_sha=base,
            final_diff=diff,
            final_diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            result={
                "pull_request": {
                    "title": "Return an empty slug",
                    "body": "## Issue\n\nFixes #3.",
                    "commit_message": "Return an empty slug\n\nFixes #3.",
                }
            },
        )
        session.add(run)
    return run


async def test_sign_in_is_required_and_the_token_is_stored_encrypted(tmp_path: Path) -> None:
    async with harness(tmp_path) as h:
        assert (await h.client.get("/api/v1/runs")).json()["error"]["code"] == "unauthenticated"
        me = (await h.client.get("/api/v1/auth/me")).json()
        assert me == {"oauth_enabled": True, "github_token_configured": False, "user": None}

        # A forged state and an open redirect are refused.
        bad = await h.client.get(
            "/api/v1/auth/github/callback", params={"code": "x", "state": "forged"}
        )
        assert bad.headers["location"].startswith("http://web.test/?auth_error=")
        login = await h.client.get("/api/v1/auth/github/login", params={"next": "//evil.example"})
        assert login.headers["location"].startswith("https://github.test/login/oauth/authorize?")

        await h.sign_in()
        me = (await h.client.get("/api/v1/auth/me")).json()
        assert me["user"]["login"] == "octo"
        assert (await h.client.get("/api/v1/runs")).status_code == 200
        async with h.factory() as session:
            credential = await session.scalar(select(GithubCredential))
        assert credential is not None
        assert OAUTH_TOKEN.encode() not in credential.encrypted_token
        assert credential.scopes == ["read:user", "repo"]

        assert (await h.client.post("/api/v1/auth/logout")).status_code == 204
        assert (await h.client.get("/api/v1/runs")).status_code == 401
        async with h.factory() as session:
            assert await session.scalar(select(GithubCredential)) is None


async def test_approval_opens_a_draft_pr_with_the_users_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with harness(tmp_path) as h:
        repo = h.fake.add_repo(FakeRepo("octo", "slugger"))
        repo.refs["main"] = repo.commit(BASE)
        diff = git_diff(tmp_path, BASE, AFTER)
        run = await _awaiting_run(h, diff, repo.refs["main"])
        await h.sign_in()

        issues = await h.client.get(f"/api/v1/repositories/{run.repository_id}/issues")
        assert issues.status_code == 200, issues.text

        stale = await h.client.post(
            f"/api/v1/runs/{run.id}/approve", json={"diff_sha256": "1" * 64}
        )
        assert stale.json()["error"]["code"] == "stale_diff"
        approved = await h.client.post(
            f"/api/v1/runs/{run.id}/approve",
            json={"diff_sha256": hashlib.sha256(diff.encode()).hexdigest()},
        )
        assert approved.json()["status"] == "approved"
        assert h.queue.published == [run.id]

        # What the worker's publish job runs:
        outcome = await publish_run(
            run.id,
            session_factory=h.factory,
            bus=InMemoryEventBus(),
            auth=h.auth,
            settings=h.settings,
        )
        assert outcome == "pull_request"

        body = (await h.client.get(f"/api/v1/runs/{run.id}")).json()
        assert body["status"] == "pr_created"
        assert body["result"]["delivery"]["url"] == "https://github.com/octo/slugger/pull/1"
        pull = (await h.client.get(f"/api/v1/runs/{run.id}/pull-request")).json()
        assert pull["branch"] == "devagent/issue-3-slugify-crashes-on-titles-without"
        assert pull["is_draft"] is True
        assert repo.commits[repo.refs[pull["branch"]]][0] == AFTER
        writes = [r for r in h.fake.requests if r.method == "POST" and "/repos/" in r.path]
        assert writes
        assert all(r.authorization == f"Bearer {OAUTH_TOKEN}" for r in writes)
        async with h.factory() as session:
            steps = list(await session.scalars(select(AgentStep).where(AgentStep.run_id == run.id)))
            assert await session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
        assert [(s.state, s.status.value) for s in steps] == [(RunStatus.CREATING_PR, "completed")]

        logs = capsys.readouterr()
        assert "pull_request_opened" in logs.out + logs.err
        assert OAUTH_TOKEN not in logs.out + logs.err


async def test_a_failed_pr_falls_back_to_a_patch_and_can_be_retried(tmp_path: Path) -> None:
    async with harness(tmp_path) as h:
        repo = h.fake.add_repo(FakeRepo("octo", "slugger", can_push=False))
        repo.refs["main"] = repo.commit(BASE)
        diff = git_diff(tmp_path, BASE, AFTER)
        run = await _awaiting_run(h, diff, repo.refs["main"])
        await h.sign_in()
        await h.client.post(
            f"/api/v1/runs/{run.id}/approve",
            json={"diff_sha256": hashlib.sha256(diff.encode()).hexdigest()},
        )

        assert (
            await publish_run(
                run.id,
                session_factory=h.factory,
                bus=InMemoryEventBus(),
                auth=h.auth,
                settings=h.settings,
            )
            == "patch"
        )
        body = (await h.client.get(f"/api/v1/runs/{run.id}")).json()
        assert body["status"] == "approved"
        assert body["result"]["delivery"]["code"] == "github_permission_denied"
        assert (await h.client.get(f"/api/v1/runs/{run.id}/pull-request")).status_code == 404

        patch = await h.client.get(f"/api/v1/runs/{run.id}/patch")
        assert (
            patch.headers["content-disposition"] == 'attachment; filename="devagent-issue-3.patch"'
        )
        assert patch.text.startswith("From 0000000000000000000000000000000000000000")
        assert "Subject: [PATCH] Return an empty slug" in patch.text
        assert diff in patch.text

        # Access fixed: retry opens the PR.
        repo.can_push = True
        retry = await h.client.post(f"/api/v1/runs/{run.id}/publish")
        assert retry.status_code == 200
        assert h.queue.published == [run.id, run.id]
        assert (
            await publish_run(
                run.id,
                session_factory=h.factory,
                bus=InMemoryEventBus(),
                auth=h.auth,
                settings=h.settings,
            )
            == "pull_request"
        )
        assert (await h.client.get(f"/api/v1/runs/{run.id}")).json()["status"] == "pr_created"
        again = await h.client.post(f"/api/v1/runs/{run.id}/publish")
        assert again.status_code == 409


async def test_without_credentials_the_run_gets_a_patch(tmp_path: Path) -> None:
    async with harness(tmp_path) as h:
        repo = h.fake.add_repo(FakeRepo("octo", "slugger"))
        repo.refs["main"] = repo.commit(BASE)
        diff = git_diff(tmp_path, BASE, AFTER)
        run = await _awaiting_run(h, diff, repo.refs["main"])
        await h.sign_in()
        await h.client.post(
            f"/api/v1/runs/{run.id}/approve",
            json={"diff_sha256": hashlib.sha256(diff.encode()).hexdigest()},
        )
        await h.client.post("/api/v1/auth/logout")  # the approver's token is gone

        assert (
            await publish_run(
                run.id,
                session_factory=h.factory,
                bus=InMemoryEventBus(),
                auth=h.auth,
                settings=h.settings,
            )
            == "patch"
        )
        async with h.factory() as session:
            stored = await session.get(AgentRun, run.id)
        assert stored is not None
        assert stored.status is RunStatus.APPROVED
        assert stored.result["delivery"]["code"] == "github_not_configured"
        assert not repo.pulls
