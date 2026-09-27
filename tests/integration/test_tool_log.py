"""Tool calls and file changes land in ``tool_calls`` / ``code_changes`` (spec 5, Phase 4)."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.event_bus import InMemoryEventBus
from backend.services.event_store import SqlEventReader
from backend.services.recorder import DbRunRecorder
from backend.services.tool_log import DbToolCallSink
from core.tools import CallStatus, ChangeType, ToolCapability
from database.models import CodeChange, ToolCallRecord
from tests.integration.test_event_store import _new_run, session_factory
from tests.tool_support import FakeSandbox, context, make_workspace
from tools.defaults import default_registry

__all__ = ["session_factory"]  # fixture

pytestmark = pytest.mark.integration


async def test_calls_and_changes_are_persisted(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    run = await _new_run(session_factory)
    workspace, base = await make_workspace(tmp_path)
    bus = InMemoryEventBus()
    recorder = DbRunRecorder(session_factory, bus, run.id)
    sink = DbToolCallSink(session_factory, run.id, workspace.root, events=recorder)
    registry = default_registry(sink)
    ctx = context(workspace, base, FakeSandbox())

    edit = await registry.call(
        "edit_file",
        {"path": "pkg/core.py", "old_str": "LIMIT = 10", "new_str": "LIMIT = 11"},
        ctx,
    )
    denied = await registry.call("read_file", {"path": "../../etc/passwd"}, ctx)
    (workspace.repo / "big.py").write_text(("y = '" + "a" * 400 + "'\n") * 1_000)
    big = await registry.call("read_file", {"path": "big.py", "end_line": 400}, ctx)
    assert edit.ok
    assert big.ok
    assert not denied.ok

    async with session_factory() as session:
        calls = (
            await session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run.id)
                .order_by(ToolCallRecord.created_at)
            )
        ).all()
        changes = (
            await session.scalars(select(CodeChange).where(CodeChange.run_id == run.id))
        ).all()

    assert [(c.tool_name, c.status) for c in calls] == [
        ("edit_file", CallStatus.OK),
        ("read_file", CallStatus.DENIED),
        ("read_file", CallStatus.OK),
    ]
    assert calls[0].capability is ToolCapability.WRITE_WORKSPACE
    assert calls[0].input == {
        "path": "pkg/core.py",
        "old_str": "LIMIT = 10",
        "new_str": "LIMIT = 11",
    }
    assert calls[1].error == {
        "code": "path_outside_workspace",
        "message": "'../../etc/passwd' contains '..'",
    }
    (change,) = changes
    assert change.tool_call_id == calls[0].id
    assert change.change_type is ChangeType.MODIFY
    assert change.path == "pkg/core.py"
    assert "+LIMIT = 11" in change.diff
    assert change.before_sha256
    assert change.after_sha256
    assert not change.is_sensitive_path

    # A large output: the model saw a truncated view; the full text is kept on disk.
    assert calls[2].output_full_ref == f"tool-outputs/{calls[2].id}.txt"
    stored = (workspace.root / calls[2].output_full_ref).read_text()
    assert len(stored) == calls[2].output_size_bytes > len(calls[2].output_truncated or "")

    # Each call was also emitted as a tool_call event for the live stream.
    page = await SqlEventReader(session_factory).read_after(run.id, 0, limit=10)
    assert page is not None
    assert [e.payload["tool_name"] for e in page.events] == ["edit_file", "read_file", "read_file"]
    assert [e.payload["status"] for e in page.events] == ["ok", "denied", "ok"]
