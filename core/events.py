"""Typed run events. Every event is persisted to ``run_events`` and streamed over SSE."""

from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.run_status import RunStatus


class EventType(StrEnum):
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    TOOL_CALL = "tool_call"
    COMMAND_OUTPUT = "command_output"
    TEST_RESULT = "test_result"
    LLM_USAGE = "llm_usage"
    STATUS_CHANGED = "status_changed"
    ERROR = "error"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    synthetic: bool = Field(
        default=False, description="True for dry-run events that did not touch a repository"
    )


class StepStarted(_Payload):
    type: Literal[EventType.STEP_STARTED] = EventType.STEP_STARTED
    step_id: UUID
    step_sequence: int
    state: RunStatus


class StepCompleted(_Payload):
    type: Literal[EventType.STEP_COMPLETED] = EventType.STEP_COMPLETED
    step_id: UUID
    step_sequence: int
    state: RunStatus
    outcome: Literal["completed", "failed", "skipped"]
    duration_ms: int
    summary: str


class ToolCall(_Payload):
    type: Literal[EventType.TOOL_CALL] = EventType.TOOL_CALL
    tool_call_id: UUID
    tool_name: str
    status: Literal["ok", "error", "denied"]
    duration_ms: int


class CommandOutput(_Payload):
    type: Literal[EventType.COMMAND_OUTPUT] = EventType.COMMAND_OUTPUT
    stream: Literal["stdout", "stderr"]
    text: str


class TestResult(_Payload):
    type: Literal[EventType.TEST_RESULT] = EventType.TEST_RESULT
    test_run_id: UUID
    passed: int
    failed: int
    errors: int
    skipped: int


class LlmUsage(_Payload):
    type: Literal[EventType.LLM_USAGE] = EventType.LLM_USAGE
    llm_call_id: UUID
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class StatusChanged(_Payload):
    type: Literal[EventType.STATUS_CHANGED] = EventType.STATUS_CHANGED
    from_status: RunStatus | None
    to_status: RunStatus
    reason: str | None = None


class ErrorEvent(_Payload):
    type: Literal[EventType.ERROR] = EventType.ERROR
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


EventPayload = Annotated[
    StepStarted
    | StepCompleted
    | ToolCall
    | CommandOutput
    | TestResult
    | LlmUsage
    | StatusChanged
    | ErrorEvent,
    Field(discriminator="type"),
]


class EventSink(Protocol):
    """Where run components emit events. Implemented by the backend's event store."""

    async def emit(self, run_id: UUID, payload: EventPayload, step_id: UUID | None = None) -> int:
        """Persist and publish ``payload``; return its per-run sequence number."""
        ...
