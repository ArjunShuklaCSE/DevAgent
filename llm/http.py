"""Shared HTTP handling for provider adapters: timeouts, bounded retries, error mapping."""

import asyncio
from typing import Any

import httpx
import structlog

from llm.types import LLMError

logger = structlog.get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """POST with retries on rate limits and server errors (exponential backoff)."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            error = LLMError("transport_error", f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            if response.status_code == 200:  # noqa: PLR2004
                data: dict[str, Any] = response.json()
                return data
            error = _status_error(response)
        if not error.retryable or attempt == max_attempts:
            raise error
        delay = base_delay_seconds * 2 ** (attempt - 1)
        logger.warning("llm_retry", attempt=attempt, code=error.code, delay_seconds=delay)
        await asyncio.sleep(delay)
    raise LLMError("unreachable", "retry loop exited")  # pragma: no cover


def _status_error(response: httpx.Response) -> LLMError:
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        message = response.text[:300]
    code = {
        400: "bad_request",
        401: "authentication_failed",
        403: "permission_denied",
        404: "model_not_found",
        429: "rate_limited",
    }.get(response.status_code, f"http_{response.status_code}")
    return LLMError(
        code,
        f"provider returned {response.status_code}: {message}",
        retryable=response.status_code in RETRYABLE_STATUS,
    )
