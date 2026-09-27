"""Migrations apply and revert cleanly, and match the models (no autogenerate drift)."""

import asyncio

import pytest
from alembic import command
from sqlalchemy import text

from database.base import Base
from database.engine import create_engine
from database.migrate import build_config
from database.models import AgentRun  # noqa: F401 - registers all tables
from tests.integration.db import temporary_database

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "github_credentials",
    "repositories",
    "issues",
    "agent_runs",
    "agent_steps",
    "run_events",
    "tool_calls",
    "llm_calls",
    "code_changes",
    "test_runs",
    "test_results",
    "approvals",
    "pull_requests",
    "eval_datasets",
    "eval_cases",
    "eval_runs",
    "eval_results",
}


async def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {r[0] for r in rows}
    finally:
        await engine.dispose()


def _alembic(url: str, *args: str) -> None:
    config = build_config(url)
    match args:
        case ("upgrade", rev):
            command.upgrade(config, rev)
        case ("downgrade", rev):
            command.downgrade(config, rev)
        case ("check",):
            command.check(config)
        case _:
            raise AssertionError(args)


async def _run(url: str, *args: str) -> None:
    # Alembic's env.py calls asyncio.run(), so run it outside this event loop.
    await asyncio.to_thread(_alembic, url, *args)


async def test_models_define_every_spec_table() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


async def test_upgrade_downgrade_upgrade() -> None:
    async with temporary_database() as url:
        await _run(url, "upgrade", "head")
        assert await _tables(url) == EXPECTED_TABLES | {"alembic_version"}

        await _run(url, "check")  # raises if models and migrations differ

        await _run(url, "downgrade", "base")
        assert await _tables(url) == {"alembic_version"}

        await _run(url, "upgrade", "head")
        assert await _tables(url) == EXPECTED_TABLES | {"alembic_version"}
