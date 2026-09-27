"""Provider-neutral request and response types.

Adapters translate these to and from each provider's wire format, so agent code never
sees a provider SDK or JSON shape.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TextBlock(_Frozen):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(_Frozen):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(_Frozen):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


class Message(_Frozen):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls(role="assistant", content=[TextBlock(text=text)])


class ToolDefinition(_Frozen):
    name: str
    description: str
    input_schema: dict[str, Any]


class LLMRequest(_Frozen):
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    # "auto": model may call tools; "any": must call one; a name: must call that one.
    tool_choice: str = "auto"
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.0, ge=0, le=2)
    # Bookkeeping for logging (component, prompt_version, attempt, step_id). Never sent
    # to a provider.
    metadata: dict[str, str] = Field(default_factory=dict)


class Usage(_Frozen):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "other"]


class LLMResponse(_Frozen):
    provider: str
    model: str
    content: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]


class LLMError(Exception):
    """A provider call failed. ``retryable`` marks rate limits and server errors."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
