"""Checks against a running stack (``docker compose up``). Run with ``pytest -m integration``."""

import os

import httpx
import pytest
from arq import create_pool
from arq.connections import RedisSettings

from backend.queue import QUEUE_NAME

pytestmark = pytest.mark.integration

API_URL = os.environ.get("DEVAGENT_TEST_API_URL", "http://localhost:8000")
REDIS_URL = os.environ.get("DEVAGENT_TEST_REDIS_URL", "redis://localhost:6379/0")


async def test_health_reports_database_and_redis_ok() -> None:
    async with httpx.AsyncClient(base_url=API_URL, timeout=10) as client:
        response = await client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


async def test_worker_consumes_jobs_from_queue() -> None:
    pool = await create_pool(RedisSettings.from_dsn(REDIS_URL), default_queue_name=QUEUE_NAME)
    try:
        job = await pool.enqueue_job("ping")
        assert job is not None
        result = await job.result(timeout=20)
    finally:
        await pool.aclose()
    assert result["status"] == "ok"
    assert result["job_id"] == job.job_id
