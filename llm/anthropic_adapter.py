"""Adapter for the Anthropic Messages API (``POST /v1/messages``)."""

from typing import Any

import httpx
from pydantic import SecretStr

from llm.http import post_json
from llm.types import (
    ContentBlock,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
)

API_VERSION = "2023-06-01"
_STOP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
}


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = "https://api.anthropic.com",
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {
            "x-api-key": self._key.get_secret_value(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        data = await post_json(self._http, self._url, headers, to_wire(request))
        return from_wire(data, request.model)

    async def aclose(self) -> None:
        await self._http.aclose()


def to_wire(request: LLMRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "system": request.system,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "messages": [_message(m) for m in request.messages],
    }
    if request.tools:
        body["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in request.tools
        ]
        body["tool_choice"] = _tool_choice(request.tool_choice)
    return body


def _tool_choice(choice: str) -> dict[str, str]:
    if choice in ("auto", "any"):
        return {"type": choice}
    return {"type": "tool", "name": choice}


def _message(message: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        else:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": blocks}


def from_wire(data: dict[str, Any], requested_model: str) -> LLMResponse:
    content: list[ContentBlock] = []
    try:
        for block in data["content"]:
            if block["type"] == "text":
                content.append(TextBlock(text=block["text"]))
            elif block["type"] == "tool_use":
                content.append(
                    ToolUseBlock(id=block["id"], name=block["name"], input=block["input"] or {})
                )
            # Other block types (e.g. thinking) are not requested and are ignored.
        usage = data.get("usage") or {}
        return LLMResponse(
            provider="anthropic",
            model=str(data.get("model") or requested_model),
            content=content,
            stop_reason=_STOP.get(str(data.get("stop_reason")), "other"),
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0))
                + int(usage.get("cache_creation_input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens", 0)),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError("invalid_response", f"unexpected response shape: {exc}") from exc
