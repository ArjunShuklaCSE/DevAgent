"""Minimal SSE client for tests: parses ``id``/``event``/``data`` fields from a stream."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SseMessage:
    id: int
    event: str
    data: dict[str, Any]


async def iter_sse(response: httpx.Response) -> AsyncIterator[SseMessage]:
    fields: dict[str, str] = {}
    async for line in response.aiter_lines():
        if line == "":
            if "id" in fields and "data" in fields:
                yield SseMessage(int(fields["id"]), fields["event"], json.loads(fields["data"]))
            fields = {}
        elif not line.startswith(":"):
            key, _, value = line.partition(": ")
            fields[key] = value
