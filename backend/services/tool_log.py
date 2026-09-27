"""Persist tool calls and file changes (``tool_calls`` and ``code_changes``, spec 5 and 12)."""

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.ports import RunRecorder
from core.events import ToolCall
from database.models import CodeChange, ToolCallRecord
from tools.registry import ToolCallLog

# Output longer than what the model saw is kept in full on disk next to the run.
OUTPUTS_DIRNAME = "tool-outputs"


class DbToolCallSink:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        run_dir: Path,
        events: RunRecorder | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._run_dir = run_dir
        self._events = events

    async def record(self, call: ToolCallLog) -> None:
        full_ref = None
        if len(call.full_output) > len(call.output_truncated):
            full_ref = await asyncio.to_thread(self._store_full_output, call)
        step_id = uuid.UUID(call.step_id) if call.step_id else None
        async with self._session_factory() as session, session.begin():
            session.add(
                ToolCallRecord(
                    id=call.tool_call_id,
                    run_id=self._run_id,
                    step_id=step_id,
                    tool_name=call.tool_name,
                    capability=call.capability,
                    input=call.arguments,
                    output_truncated=call.output_truncated,
                    output_size_bytes=len(call.full_output.encode("utf-8")),
                    output_full_ref=full_ref,
                    status=call.status,
                    error=call.error,
                    duration_ms=call.duration_ms,
                )
            )
            await session.flush()  # the FK from code_changes needs the tool call row
            for change in call.changes:
                session.add(
                    CodeChange(
                        run_id=self._run_id,
                        step_id=step_id,
                        tool_call_id=call.tool_call_id,
                        path=change.path,
                        change_type=change.change_type,
                        before_sha256=change.before_sha256,
                        after_sha256=change.after_sha256,
                        diff=change.diff,
                        is_sensitive_path=change.is_sensitive,
                    )
                )
        if self._events is not None:
            await self._events.emit(
                ToolCall(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    status=call.status.value,
                    duration_ms=call.duration_ms,
                )
            )

    def _store_full_output(self, call: ToolCallLog) -> str:
        directory = self._run_dir / OUTPUTS_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{call.tool_call_id}.txt"
        (directory / name).write_text(call.full_output, encoding="utf-8")
        return f"{OUTPUTS_DIRNAME}/{name}"
