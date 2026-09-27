"""Structured outputs validated by Pydantic, with bounded retries (spec 7.3).

The model is required to answer by calling a single ``submit`` tool whose input schema
is the output model's JSON schema. The tool input is validated; if it is missing or
invalid, the validation errors are sent back as a tool result and the model tries
again, up to ``max_attempts`` in total. Every attempt is a separate, logged LLM call.
"""

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llm.client import LLMClient
from llm.types import (
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

SUBMIT_TOOL = "submit"
MAX_ERROR_CHARS = 2000


class StructuredOutput(BaseModel):
    """Base for every component's output: always carries a short rationale (spec 4.4)."""

    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(
        min_length=1, max_length=800, description="1-3 sentences explaining the decision"
    )


class StructuredOutputError(Exception):
    def __init__(self, attempts: int, last_error: str) -> None:
        super().__init__(f"no valid structured output after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


@dataclass
class StructuredResult[T: StructuredOutput]:
    value: T
    attempts: int
    usage: Usage
    responses: list[LLMResponse] = field(default_factory=list)


def submit_tool(schema: type[StructuredOutput], description: str) -> ToolDefinition:
    json_schema = schema.model_json_schema()
    json_schema.pop("title", None)
    return ToolDefinition(name=SUBMIT_TOOL, description=description, input_schema=json_schema)


async def complete_structured[T: StructuredOutput](
    client: LLMClient,
    request: LLMRequest,
    schema: type[T],
    *,
    max_attempts: int = 3,
    description: str = "Submit your final answer.",
) -> StructuredResult[T]:
    tool = submit_tool(schema, description)
    messages = list(request.messages)
    usage = Usage()
    responses: list[LLMResponse] = []
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        attempt_request = request.model_copy(
            update={
                "messages": messages,
                "tools": [tool],
                "tool_choice": SUBMIT_TOOL,
                "metadata": {**request.metadata, "attempt": str(attempt)},
            }
        )
        response = await client.complete(attempt_request)
        responses.append(response)
        usage = usage + response.usage
        submitted = next((b for b in response.tool_uses if b.name == SUBMIT_TOOL), None)
        if submitted is None:
            last_error = "the answer must be given by calling the submit tool"
            messages = [*messages, _assistant(response), Message.user(last_error)]
            continue
        try:
            value = schema.model_validate(submitted.input)
        except ValidationError as exc:
            last_error = _format_errors(exc)
            messages = [
                *messages,
                _assistant(response),
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(
                            tool_use_id=submitted.id,
                            content=f"Invalid answer; fix these problems and submit again:\n"
                            f"{last_error}",
                            is_error=True,
                        )
                    ],
                ),
            ]
            continue
        return StructuredResult(value=value, attempts=attempt, usage=usage, responses=responses)
    raise StructuredOutputError(max_attempts, last_error)


def _assistant(response: LLMResponse) -> Message:
    content = list(response.content) or [TextBlock(text="(no content)")]
    return Message(role="assistant", content=content)


def _format_errors(exc: ValidationError) -> str:
    lines = [
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors(include_url=False)
    ]
    return "\n".join(lines)[:MAX_ERROR_CHARS]


def tool_input_json(block: ToolUseBlock) -> str:
    return json.dumps(block.input, sort_keys=True)
