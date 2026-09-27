"""Provider adapters against mocked HTTP (no keys needed): wire format, parsing, retries."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from llm.anthropic_adapter import AnthropicClient
from llm.openai_adapter import OpenAIClient
from llm.types import (
    LLMError,
    LLMRequest,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

KEY = SecretStr("sk-test-not-a-real-key")
TOOL = ToolDefinition(
    name="read_file",
    description="Read a file",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
)
REQUEST = LLMRequest(
    model="some-model",
    system="policy",
    messages=[
        Message.user("fix the bug"),
        Message(
            role="assistant",
            content=[
                TextBlock(text="reading"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
            ],
        ),
        Message(role="user", content=[ToolResultBlock(tool_use_id="t1", content="x = 1")]),
    ],
    tools=[TOOL],
    tool_choice="any",
    max_tokens=512,
    metadata={"component": "editor"},
)


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[index].content)
        return data


def http(recorder: Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder))


ANTHROPIC_OK = {
    "model": "some-model",
    "stop_reason": "tool_use",
    "content": [
        {"type": "text", "text": "I will edit."},
        {"type": "tool_use", "id": "t2", "name": "edit_file", "input": {"path": "a.py"}},
    ],
    "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 5},
}


async def test_anthropic_wire_format_and_parsing() -> None:
    recorder = Recorder(httpx.Response(200, json=ANTHROPIC_OK))
    client = AnthropicClient(KEY, http=http(recorder))
    response = await client.complete(REQUEST)

    sent = recorder.requests[0]
    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == KEY.get_secret_value()
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = recorder.body()
    assert body["system"] == "policy"
    assert body["tool_choice"] == {"type": "any"}
    assert body["tools"][0]["input_schema"] == TOOL.input_schema
    assert body["messages"][1]["content"][1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "read_file",
        "input": {"path": "a.py"},
    }
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert "metadata" not in body  # bookkeeping never leaves the process

    assert response.text == "I will edit."
    assert response.tool_uses[0].name == "edit_file"
    assert response.stop_reason == "tool_use"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (125, 30)


async def test_openai_wire_format_and_parsing() -> None:
    recorder = Recorder(
        httpx.Response(
            200,
            json={
                "model": "some-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "edit_file",
                                        "arguments": '{"path": "a.py"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 20},
            },
        )
    )
    client = OpenAIClient(KEY, http=http(recorder))
    response = await client.complete(REQUEST)

    sent = recorder.requests[0]
    assert sent.url == "https://api.openai.com/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY.get_secret_value()}"
    body = recorder.body()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a.py"}',
    }
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "x = 1"}
    assert body["tool_choice"] == "required"
    assert body["max_completion_tokens"] == 512

    assert response.tool_uses[0].input == {"path": "a.py"}
    assert response.stop_reason == "tool_use"
    assert response.usage.total == 220


async def test_named_tool_choice() -> None:
    request = REQUEST.model_copy(update={"tool_choice": "submit"})
    recorder = Recorder(httpx.Response(200, json=ANTHROPIC_OK))
    await AnthropicClient(KEY, http=http(recorder)).complete(request)
    assert recorder.body()["tool_choice"] == {"type": "tool", "name": "submit"}


async def test_rate_limit_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def no_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("llm.http.asyncio.sleep", no_sleep)
    recorder = Recorder(
        httpx.Response(429, json={"error": {"message": "slow down"}}),
        httpx.Response(529, json={"error": {"message": "overloaded"}}),
        httpx.Response(200, json=ANTHROPIC_OK),
    )
    response = await AnthropicClient(KEY, http=http(recorder)).complete(REQUEST)
    assert response.text == "I will edit."
    assert delays == [1.0, 2.0]


async def test_auth_errors_are_not_retried() -> None:
    recorder = Recorder(httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(LLMError) as info:
        await AnthropicClient(KEY, http=http(recorder)).complete(REQUEST)
    assert info.value.code == "authentication_failed"
    assert not info.value.retryable
    assert KEY.get_secret_value() not in str(info.value)
    assert len(recorder.requests) == 1


async def test_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("llm.http.asyncio.sleep", no_sleep)
    recorder = Recorder(*(httpx.Response(503, text="down") for _ in range(3)))
    with pytest.raises(LLMError) as info:
        await OpenAIClient(KEY, http=http(recorder)).complete(REQUEST)
    assert info.value.code == "http_503"
    assert len(recorder.requests) == 3


async def test_malformed_response() -> None:
    recorder = Recorder(httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError) as info:
        await AnthropicClient(KEY, http=http(recorder)).complete(REQUEST)
    assert info.value.code == "invalid_response"


async def test_openai_unparseable_tool_arguments_are_kept_as_data() -> None:
    recorder = Recorder(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "function": {"name": "submit", "arguments": "{not json"},
                                }
                            ]
                        },
                    }
                ]
            },
        )
    )
    response = await OpenAIClient(KEY, http=http(recorder)).complete(REQUEST)
    assert response.tool_uses[0].input == {"_unparseable_arguments": "{not json"}
