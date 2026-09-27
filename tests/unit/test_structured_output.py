"""Structured outputs: validation against Pydantic, bounded retries, ScriptedLLM checks."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from llm.scripted import ScriptedLLM, ScriptedToolCall, ScriptMismatchError, ScriptStep
from llm.structured import (
    SUBMIT_TOOL,
    StructuredOutput,
    StructuredOutputError,
    complete_structured,
)
from llm.types import LLMRequest, Message, ToolResultBlock


class Plan(StructuredOutput):
    steps: list[str] = Field(min_length=1)
    files: list[str]


REQUEST = LLMRequest(
    model="scripted",
    system="policy",
    messages=[Message.user("plan it")],
    metadata={"component": "planner"},
)
GOOD = {"rationale": "Fix the range bound.", "steps": ["edit chunk()"], "files": ["pkg/core.py"]}


def submit(payload: dict[str, Any]) -> ScriptStep:
    return ScriptStep(tool_calls=[ScriptedToolCall(name=SUBMIT_TOOL, input=payload)])


async def test_valid_first_attempt() -> None:
    llm = ScriptedLLM([submit(GOOD)])
    result = await complete_structured(llm, REQUEST, Plan)
    assert result.value == Plan.model_validate(GOOD)
    assert result.attempts == 1
    sent = llm.requests[0]
    assert sent.tool_choice == SUBMIT_TOOL
    assert sent.tools[0].input_schema["required"] == ["rationale", "steps", "files"]
    assert sent.metadata == {"component": "planner", "attempt": "1"}


async def test_invalid_output_is_retried_with_the_errors() -> None:
    llm = ScriptedLLM(
        [
            submit({"rationale": "", "steps": [], "files": "one"}),
            submit(GOOD),
        ]
    )
    result = await complete_structured(llm, REQUEST, Plan)
    assert result.attempts == 2
    assert result.usage.total == 300
    retry = llm.requests[1]
    assert retry.metadata["attempt"] == "2"
    feedback = retry.messages[-1].content[0]
    assert isinstance(feedback, ToolResultBlock)
    assert feedback.is_error
    for field in ("rationale", "steps", "files"):
        assert f"- {field}:" in feedback.content


async def test_answer_without_the_submit_tool_is_retried() -> None:
    llm = ScriptedLLM([ScriptStep(text="Here is my plan: ..."), submit(GOOD)])
    result = await complete_structured(llm, REQUEST, Plan)
    assert result.attempts == 2
    assert "submit tool" in llm.requests[1].messages[-1].content[0].model_dump()["text"]


async def test_unknown_fields_are_rejected() -> None:
    llm = ScriptedLLM([submit({**GOOD, "push_to": "main"}), submit(GOOD)])
    result = await complete_structured(llm, REQUEST, Plan)
    assert result.attempts == 2


async def test_retries_are_bounded() -> None:
    llm = ScriptedLLM([submit({"rationale": "x"}) for _ in range(5)])
    with pytest.raises(StructuredOutputError) as info:
        await complete_structured(llm, REQUEST, Plan, max_attempts=3)
    assert info.value.attempts == 3
    assert llm.remaining == 2  # exactly three calls were made


async def test_script_expectations_are_checked(tmp_path: Path) -> None:
    cassette = tmp_path / "plan.yaml"
    cassette.write_text(
        "steps:\n"
        "  - expect_component: planner\n"
        "    expect_in_prompt: ['plan it']\n"
        "    expect_tool_choice: submit\n"
        "    tool_calls:\n"
        "      - name: submit\n"
        "        input: {rationale: ok, steps: [a], files: []}\n"
    )
    result = await complete_structured(ScriptedLLM.from_yaml(cassette), REQUEST, Plan)
    assert result.value.steps == ["a"]

    wrong = REQUEST.model_copy(update={"metadata": {"component": "editor"}})
    with pytest.raises(ScriptMismatchError, match="expected component 'planner'"):
        await complete_structured(ScriptedLLM.from_yaml(cassette), wrong, Plan)
