"""Tool registry: lookup, argument validation, limits, error mapping and call logging.

``ToolRegistry.call`` never raises for tool-level problems. Unknown tools, invalid
arguments, policy denials and tool errors all come back as a ``ToolResult`` with
``ok=False`` and a stable ``error_code``, so the agent sees a structured error and
every attempt, including denied ones, is logged (spec 5 and 6.3).
"""

import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from pydantic import ValidationError

from core.tools import CallStatus, ToolCapability
from sandbox.docker_sandbox import SandboxError
from sandbox.policy import PolicyViolationError
from tools.base import BaseTool, FileChange, ToolContext, ToolInput
from tools.errors import ToolError

logger = structlog.get_logger(__name__)

TRUNCATION_NOTICE = "\n[... output truncated: {omitted} more characters ...]"


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: uuid.UUID
    tool_name: str
    status: CallStatus
    output: str  # what the model sees (truncated)
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    changes: tuple[FileChange, ...] = ()
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is CallStatus.OK


@dataclass(frozen=True)
class ToolCallLog:
    """Everything recorded about one call (``tool_calls`` + ``code_changes`` rows)."""

    tool_call_id: uuid.UUID
    run_id: str
    step_id: str | None
    tool_name: str
    capability: ToolCapability
    arguments: dict[str, Any]
    status: CallStatus
    output_truncated: str
    full_output: str
    error: dict[str, Any] | None
    duration_ms: int
    changes: tuple[FileChange, ...]


class ToolCallSink(Protocol):
    async def record(self, call: ToolCallLog) -> None: ...


class NullSink:
    async def record(self, call: ToolCallLog) -> None:
        return None


class ToolRegistry:
    def __init__(self, tools: Iterable[BaseTool[Any]], sink: ToolCallSink | None = None) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name {tool.name!r}")
            self._tools[tool.name] = tool
        self._sink = sink or NullSink()

    def with_sink(self, sink: ToolCallSink) -> "ToolRegistry":
        return ToolRegistry(self._tools.values(), sink)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> BaseTool[Any]:
        return self._tools[name]

    def specs(self, capabilities: Sequence[ToolCapability] | None = None) -> list[dict[str, Any]]:
        """Tool definitions for an LLM request (name, description, JSON schema)."""
        allowed = set(capabilities) if capabilities is not None else None
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "capability": tool.capability.value,
                "input_schema": tool.json_schema(),
            }
            for name, tool in sorted(self._tools.items())
            if allowed is None or tool.capability in allowed
        ]

    def _require(self, tool: BaseTool[Any] | None, name: str) -> BaseTool[Any]:
        if tool is None:
            raise ToolError("unknown_tool", f"no tool named {name!r}; tools: {self.names}")
        return tool

    async def call(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        call_id = uuid.uuid4()
        started = time.monotonic()
        log = logger.bind(
            run_id=ctx.run_id, step_id=ctx.step_id, tool=name, tool_call_id=str(call_id)
        )
        tool = self._tools.get(name)
        capability = tool.capability if tool is not None else ToolCapability.READ

        data: dict[str, Any] = {}
        changes: tuple[FileChange, ...] = ()
        error: dict[str, Any] | None = None
        try:
            tool = self._require(tool, name)
            args: ToolInput = _parse(tool, arguments)
            output = await tool.run(args, ctx)
            status, full_text, data, changes = (
                CallStatus.OK,
                output.text,
                output.data,
                output.changes,
            )
        except ToolError as exc:
            status, full_text = exc.status, f"error [{exc.code}]: {exc.message}"
            error = {"code": exc.code, "message": exc.message}
        except PolicyViolationError as exc:
            status, full_text = CallStatus.DENIED, f"denied [{exc.code}]: {exc}"
            error = {"code": exc.code, "message": str(exc)}
        except SandboxError as exc:
            status, full_text = CallStatus.ERROR, f"error [{exc.code}]: {exc}"
            error = {"code": exc.code, "message": str(exc)}

        limit = tool.max_output_chars if tool is not None else 2_000
        shown = _truncate(full_text, limit)
        duration_ms = int((time.monotonic() - started) * 1000)
        result = ToolResult(
            tool_call_id=call_id,
            tool_name=name,
            status=status,
            output=shown,
            data=data,
            error_code=error["code"] if error else None,
            changes=changes,
            duration_ms=duration_ms,
        )
        await self._sink.record(
            ToolCallLog(
                tool_call_id=call_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                tool_name=name,
                capability=capability,
                arguments=_json_safe(arguments),
                status=status,
                output_truncated=shown,
                full_output=full_text,
                error=error,
                duration_ms=duration_ms,
                changes=changes,
            )
        )
        log.info(
            "tool_call",
            status=status.value,
            error_code=result.error_code,
            duration_ms=duration_ms,
            output_chars=len(full_text),
            changes=len(changes),
        )
        return result


def _parse(tool: BaseTool[Any], arguments: dict[str, Any]) -> ToolInput:
    if not isinstance(arguments, dict):
        raise ToolError("invalid_arguments", "arguments must be a JSON object")
    try:
        parsed: ToolInput = tool.parse(arguments)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ToolError("invalid_arguments", details) from exc
    return parsed


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_NOTICE.format(omitted=len(text) - limit)


def _json_safe(arguments: object) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return {str(k): v for k, v in arguments.items()}
    return {"_invalid": repr(arguments)[:1000]}
