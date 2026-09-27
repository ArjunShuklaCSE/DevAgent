"""Request validation that never reaches the database."""

from uuid import uuid4

import pytest

from backend.config import Settings
from backend.schemas import RepositoryCreate, RunBudget
from tests.conftest import StaticProbe, client_for


@pytest.mark.parametrize(
    ("url", "owner", "name"),
    [
        ("https://github.com/pallets/click", "pallets", "click"),
        ("https://github.com/pallets/click.git", "pallets", "click"),
        ("https://github.com/a-b/c.d_e/", "a-b", "c.d_e"),
    ],
)
def test_accepts_github_urls(url: str, owner: str, name: str) -> None:
    assert RepositoryCreate(url=url).owner_and_name() == (owner, name)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r",
        "https://gitlab.com/o/r",
        "https://github.com/o",
        "https://github.com/o/r/tree/main",
        "file:///etc/passwd",
        "https://github.com/o/r?x=1",
        "git@github.com:o/r.git",
    ],
)
def test_rejects_non_github_urls(url: str) -> None:
    with pytest.raises(ValueError, match="GitHub repository URL"):
        RepositoryCreate(url=url)


def test_budget_defaults_match_spec() -> None:
    budget = RunBudget()
    assert budget.max_steps == 40
    assert budget.max_fix_attempts == 3
    assert budget.max_tokens == 400_000
    assert str(budget.max_cost_usd) == "2.00"
    assert budget.command_timeout_seconds == 300
    assert budget.wall_clock_seconds == 1800


async def test_create_run_rejects_unavailable_mode(settings: Settings) -> None:
    body = {"repository_id": str(uuid4()), "issue": {"title": "t"}, "mode": "agent"}
    async with client_for(settings, [StaticProbe("database")]) as client:
        response = await client.post("/api/v1/runs", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_create_run_rejects_budget_out_of_range(settings: Settings) -> None:
    body = {
        "repository_id": str(uuid4()),
        "issue": {"title": "t"},
        "budget": {"max_cost_usd": -1},
    }
    async with client_for(settings, [StaticProbe("database")]) as client:
        response = await client.post("/api/v1/runs", json=body)
    assert response.status_code == 422


async def test_events_rejects_malformed_last_event_id(settings: Settings) -> None:
    async with client_for(settings, [StaticProbe("database")]) as client:
        response = await client.get(
            f"/api/v1/runs/{uuid4()}/events", headers={"Last-Event-ID": "not-a-number"}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_last_event_id"
