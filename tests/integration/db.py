"""Helpers for integration tests that need their own Postgres database."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg

ADMIN_DSN = os.environ.get(
    "DEVAGENT_TEST_ADMIN_DSN", "postgresql://devagent:devagent@localhost:5432/postgres"
)


def _dsn_for(database: str) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{database}"


@asynccontextmanager
async def temporary_database() -> AsyncIterator[str]:
    """Create an empty database and yield its SQLAlchemy asyncpg URL; drop it afterwards."""
    name = f"devagent_test_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    try:
        yield _dsn_for(name).replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()
