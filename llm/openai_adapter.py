"""Adapter for the OpenAI Chat Completions API (``POST /v1/chat/completions``)."""

import json
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

_STOP: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class OpenAIClient:
    provider = "openai"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = "https://api.openai.com",
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {
            "authorization": f"Bearer {self._key.get_secret_value()}",
            "content-type": "application/json",
        }
        data = await post_json(self._http, self._url, headers, to_wire(request))
        return from_wire(data, request.model)

    async def aclose(self) -> None:
        await self._http.aclose()


def to_wire(request: LLMRequest) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
    for message in request.messages:
        messages += _messages(message)
    body: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_completion_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in request.tools
        ]
        body["tool_choice"] = _tool_choice(request.tool_choice)
    return body


def _tool_choice(choice: str) -> str | dict[str, Any]:
    if choice == "auto":
        return "auto"
    if choice == "any":
        return "required"
    return {"type": "function", "function": {"name": choice}}


def _messages(message: Message) -> list[dict[str, Any]]:
    """One neutral message can become several OpenAI messages (tool results are separate)."""
    out: list[dict[str, Any]] = []
    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
    if message.role == "assistant":
        entry: dict[str, Any] = {"role": "assistant", "content": text or None}
        calls = [
            {
                "id": b.id,
                "type": "function",
                "function": {"name": b.name, "arguments": json.dumps(b.input)},
            }
            for b in message.content
            if isinstance(b, ToolUseBlock)
        ]
        if calls:
            entry["tool_calls"] = calls
        return [entry]
    for block in message.content:
        if block.type == "tool_result":
            out.append(
                {"role": "tool", "tool_call_id": block.tool_use_id, "content": block.content}
            )
    if text:
        out.append({"role": "user", "content": text})
    return out


def from_wire(data: dict[str, Any], requested_model: str) -> LLMResponse:
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content: list[ContentBlock] = []
        if message.get("content"):
            content.append(TextBlock(text=message["content"]))
        for call in message.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparseable_arguments": call["function"]["arguments"]}
            if not isinstance(arguments, dict):
                arguments = {"_unparseable_arguments": arguments}
            content.append(
                ToolUseBlock(id=call["id"], name=call["function"]["name"], input=arguments)
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            provider="openai",
            model=str(data.get("model") or requested_model),
            content=content,
            stop_reason=_STOP.get(str(choice.get("finish_reason")), "other"),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("invalid_response", f"unexpected response shape: {exc}") from exc
