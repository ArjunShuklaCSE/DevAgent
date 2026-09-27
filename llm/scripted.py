"""``ScriptedLLM``: a deterministic ``LLMClient`` that replays recorded responses.

Used by unit and integration tests so CI needs no API keys (spec 14). A script is a list
of steps; each step can check the request it answers (component, required text, tool
choice) and returns text and/or tool calls with a fixed token usage. Scripts can live
in YAML cassettes (``ScriptedLLM.from_yaml``).
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from llm.types import (
    ContentBlock,
    LLMError,
    LLMRequest,
    LLMResponse,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
)


class ScriptedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ScriptStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Expectations about the request this step answers (checked, not just documented).
    expect_component: str | None = None
    expect_in_prompt: list[str] = Field(default_factory=list)
    expect_tool_choice: str | None = None
    # The response.
    text: str = ""
    tool_calls: list[ScriptedToolCall] = Field(default_factory=list)
    stop_reason: StopReason | None = None
    input_tokens: int = 100
    output_tokens: int = 50


class ScriptMismatchError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__("script_mismatch", message)


class ScriptedLLM:
    provider = "scripted"

    def __init__(self, steps: list[ScriptStep]) -> None:
        self._steps = list(steps)
        self._position = 0
        self.requests: list[LLMRequest] = []

    @classmethod
    def from_yaml(cls, path: Path) -> "ScriptedLLM":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls([ScriptStep.model_validate(step) for step in raw["steps"]])

    @property
    def remaining(self) -> int:
        return len(self._steps) - self._position

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self._position >= len(self._steps):
            raise LLMError(
                "script_exhausted", f"no scripted response for call {self._position + 1}"
            )
        step = self._steps[self._position]
        self._position += 1
        self._check(step, request)
        content: list[ContentBlock] = []
        if step.text:
            content.append(TextBlock(text=step.text))
        for index, call in enumerate(step.tool_calls):
            content.append(
                ToolUseBlock(id=f"call_{self._position}_{index}", name=call.name, input=call.input)
            )
        stop = step.stop_reason or ("tool_use" if step.tool_calls else "end_turn")
        return LLMResponse(
            provider=self.provider,
            model=request.model,
            content=content,
            stop_reason=stop,
            usage=Usage(input_tokens=step.input_tokens, output_tokens=step.output_tokens),
        )

    def _check(self, step: ScriptStep, request: LLMRequest) -> None:
        where = f"scripted call {self._position}"
        component = request.metadata.get("component")
        if step.expect_component is not None and component != step.expect_component:
            raise ScriptMismatchError(
                f"{where}: expected component {step.expect_component!r}, got {component!r}"
            )
        if step.expect_tool_choice is not None and request.tool_choice != step.expect_tool_choice:
            raise ScriptMismatchError(
                f"{where}: expected tool_choice {step.expect_tool_choice!r}, "
                f"got {request.tool_choice!r}"
            )
        prompt = request.system + "\n" + request.model_dump_json(include={"messages"})
        for needle in step.expect_in_prompt:
            if needle not in prompt:
                raise ScriptMismatchError(f"{where}: prompt does not contain {needle!r}")
