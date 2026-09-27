"""Every tool from spec 5, against a real git workspace (sandbox faked)."""

from pathlib import Path
from typing import Any

import pytest

from core.tools import CallStatus, ChangeType, ToolCapability
from tests.tool_support import FakeSandbox, context, make_workspace
from tools.base import ToolContext
from tools.defaults import default_registry
from tools.registry import ToolCallLog, ToolRegistry, ToolResult


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[ToolCallLog] = []

    async def record(self, call: ToolCallLog) -> None:
        self.calls.append(call)


@pytest.fixture
async def env(tmp_path: Path) -> tuple[ToolRegistry, ToolContext, RecordingSink, FakeSandbox]:
    workspace, base = await make_workspace(tmp_path)
    sink = RecordingSink()
    sandbox = FakeSandbox()
    return default_registry(sink), context(workspace, base, sandbox), sink, sandbox


Env = tuple[ToolRegistry, ToolContext, RecordingSink, FakeSandbox]


async def call(env: Env, tool: str, **arguments: Any) -> ToolResult:
    registry, ctx, _, _ = env
    return await registry.call(tool, arguments, ctx)


# ------------------------------------------------------------------ registry
def test_registry_exposes_all_spec_tools_with_schemas() -> None:
    registry = default_registry()
    assert registry.names == sorted(
        [
            "list_tree", "search_files", "search_text", "find_symbol", "find_references",
            "read_file", "edit_file", "create_file", "run_command", "run_tests", "git_diff",
        ]
    )  # fmt: skip
    specs = {s["name"]: s for s in registry.specs()}
    assert specs["edit_file"]["capability"] == "write_workspace"
    assert specs["run_command"]["capability"] == "execute_sandbox"
    assert set(specs["edit_file"]["input_schema"]["required"]) == {"path", "old_str", "new_str"}
    assert specs["read_file"]["input_schema"]["additionalProperties"] is False
    read_only = registry.specs([ToolCapability.READ])
    assert {s["capability"] for s in read_only} == {"read"}


async def test_unknown_tool_and_invalid_arguments_are_structured_errors(env: Env) -> None:
    result = await call(env, "delete_everything")
    assert result.status is CallStatus.ERROR
    assert result.error_code == "unknown_tool"
    result = await call(env, "read_file", path="README.md", evil=True)
    assert result.error_code == "invalid_arguments"
    assert "evil" in result.output
    result = await call(env, "read_file", path="README.md", start_line=0)
    assert result.error_code == "invalid_arguments"
    sink = env[2]
    assert [c.status for c in sink.calls] == [CallStatus.ERROR] * 3  # failures are logged too


async def test_every_call_is_logged_with_truncated_and_full_output(env: Env) -> None:
    registry, ctx, sink, _ = env
    long_text = "x = 1\n" * 20_000
    (ctx.repo / "big.py").write_text(long_text)
    result = await registry.call(
        "read_file", {"path": "big.py", "start_line": 1, "end_line": 400}, ctx
    )
    assert result.ok
    log = sink.calls[-1]
    assert log.tool_name == "read_file"
    assert log.capability is ToolCapability.READ
    assert log.arguments == {"path": "big.py", "start_line": 1, "end_line": 400}
    assert log.run_id == "run-1"
    assert log.duration_ms >= 0
    assert log.output_truncated == result.output
    assert len(log.full_output) >= len(log.output_truncated)


# ------------------------------------------------------------------ path safety
@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("../outside.txt", "path_outside_workspace"),
        ("pkg/../../outside.txt", "path_outside_workspace"),
        ("/etc/passwd", "path_outside_workspace"),
        ("/workspace/../etc/passwd", "path_outside_workspace"),
        ("pkg/\x00core.py", "invalid_path"),
        ("", "invalid_path"),
        ("missing.py", "not_found"),
    ],
)
async def test_read_rejects_traversal(env: Env, path: str, code: str) -> None:
    result = await call(env, "read_file", path=path)
    assert result.error_code == code
    if code.startswith("path"):
        assert result.status is CallStatus.DENIED


async def test_symlink_escape_is_rejected(env: Env, tmp_path: Path) -> None:
    ctx = env[1]
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOKEN=abc\n")
    (ctx.repo / "link.txt").symlink_to(secret)
    (ctx.repo / "linkdir").symlink_to(tmp_path)
    for tool, args in [
        ("read_file", {"path": "link.txt"}),
        ("read_file", {"path": "linkdir/host-secret.txt"}),
        ("edit_file", {"path": "link.txt", "old_str": "TOKEN", "new_str": "X"}),
        ("create_file", {"path": "linkdir/new.py", "content": "x"}),
        ("search_text", {"pattern": "TOKEN", "path": "linkdir"}),
        ("list_tree", {"path": "linkdir"}),
    ]:
        result = await call(env, tool, **args)
        assert result.status is CallStatus.DENIED, (tool, result.output)
        assert result.error_code in {"symlink_escape", "symlink_write_denied"}, (
            tool,
            result.output,
        )
    assert secret.read_text() == "TOKEN=abc\n"
    assert not (tmp_path / "new.py").exists()


async def test_symlink_inside_workspace_can_be_read_not_written_through(env: Env) -> None:
    ctx = env[1]
    (ctx.repo / "alias.py").symlink_to(ctx.repo / "pkg" / "core.py")
    assert (await call(env, "read_file", path="alias.py", end_line=1)).ok
    result = await call(
        env, "edit_file", path="alias.py", old_str="LIMIT = 10", new_str="LIMIT = 11"
    )
    assert result.error_code == "symlink_write_denied"


# ------------------------------------------------------------------ read_file
async def test_read_file_ranges(env: Env) -> None:
    result = await call(env, "read_file", path="pkg/core.py", start_line=4, end_line=6)
    assert result.ok
    assert result.output.splitlines()[:3] == [
        "4| class Wallet:",
        "5|     def __init__(self, balance: int) -> None:",
        "6|         self.balance = balance",
    ]
    assert result.data == {"path": "pkg/core.py", "start_line": 4, "end_line": 6, "total_lines": 16}
    assert (
        await call(env, "read_file", path="pkg/core.py", start_line=1, end_line=401)
    ).error_code == "range_too_large"
    assert (
        await call(env, "read_file", path="pkg/core.py", start_line=99)
    ).error_code == "invalid_range"
    assert (await call(env, "read_file", path="logo.png")).error_code == "binary_file"
    assert (await call(env, "read_file", path="pkg")).error_code == "not_a_file"


# ------------------------------------------------------------------ edit_file
async def test_edit_file_records_hashes_and_diff(env: Env) -> None:
    ctx = env[1]
    result = await call(
        env,
        "edit_file",
        path="pkg/core.py",
        old_str="range(0, len(items) - size, size)",
        new_str="range(0, len(items), size)",
    )
    assert result.ok, result.output
    (change,) = result.changes
    assert change.change_type is ChangeType.MODIFY
    assert change.path == "pkg/core.py"
    assert change.before_sha256 != change.after_sha256
    assert (
        "-    return [items[i : i + size] for i in range(0, len(items) - size, size)]"
        in change.diff
    )
    assert "+    return [items[i : i + size] for i in range(0, len(items), size)]" in change.diff
    assert not change.is_sensitive
    assert "range(0, len(items), size)" in (ctx.repo / "pkg/core.py").read_text()
    assert env[2].calls[-1].changes == (change,)


async def test_edit_rejects_zero_and_ambiguous_matches(env: Env) -> None:
    ctx = env[1]
    before = (ctx.repo / "pkg/core.py").read_text()
    none = await call(env, "edit_file", path="pkg/core.py", old_str="does not exist", new_str="x")
    assert none.error_code == "no_match"
    many = await call(
        env, "edit_file", path="pkg/core.py", old_str="self.balance", new_str="self.b"
    )
    assert many.error_code == "ambiguous_match"
    assert "occurs 4 times" in many.output
    assert "lines 6, 9, 11, 12" in many.output
    same = await call(
        env, "edit_file", path="pkg/core.py", old_str="LIMIT = 10", new_str="LIMIT = 10"
    )
    assert same.error_code == "no_change"
    assert (ctx.repo / "pkg/core.py").read_text() == before  # nothing written
    assert all(not r.changes for r in (none, many, same))


async def test_protected_and_blocked_paths(env: Env, tmp_path: Path) -> None:
    ci = await call(
        env, "edit_file", path=".github/workflows/ci.yml", old_str="push", new_str="pull_request"
    )
    assert ci.status is CallStatus.DENIED
    assert ci.error_code == "protected_path"
    git_config = await call(env, "create_file", path=".git/hooks/pre-commit", content="#!/bin/sh\n")
    assert git_config.error_code == "blocked_path"
    lock = await call(env, "create_file", path="uv.lock", content="")
    assert lock.error_code == "protected_path"

    # When the approved plan names the file, the edit goes through and is flagged.
    registry, ctx, _, sandbox = env
    allowed = context(
        ctx.workspace, ctx.base_commit, sandbox, frozenset({".github/workflows/ci.yml"})
    )
    ok = await registry.call(
        "edit_file",
        {"path": ".github/workflows/ci.yml", "old_str": "push", "new_str": "pull_request"},
        allowed,
    )
    assert ok.ok
    assert ok.changes[0].path_class == "protected"
    assert ok.changes[0].is_sensitive


async def test_dependency_manifest_edits_are_flagged(env: Env) -> None:
    result = await call(
        env, "edit_file", path="requirements.txt", old_str="pytest\n", new_str="pytest\nrequests\n"
    )
    assert result.ok
    assert result.changes[0].path_class == "sensitive"


# ------------------------------------------------------------------ create_file
async def test_create_file(env: Env) -> None:
    ctx = env[1]
    result = await call(
        env, "create_file", path="tests/test_repro.py", content="def test_x():\n    assert False\n"
    )
    assert result.ok
    (change,) = result.changes
    assert change.change_type is ChangeType.CREATE
    assert change.before_sha256 is None
    assert change.diff.startswith("--- /dev/null\n+++ b/tests/test_repro.py")
    assert (ctx.repo / "tests/test_repro.py").exists()
    again = await call(env, "create_file", path="tests/test_repro.py", content="x")
    assert again.error_code == "already_exists"
    nested = await call(env, "create_file", path="new/pkg/mod.py", content="X = 1\n")
    assert nested.ok
    assert (
        await call(env, "create_file", path="pkg/core.py/x.py", content="")
    ).error_code == "not_a_directory"


# ------------------------------------------------------------------ listing and search
async def test_list_tree_respects_gitignore_and_skips_noise(env: Env) -> None:
    result = await call(env, "list_tree", depth=2)
    assert result.ok
    text = result.output
    for shown in ("pkg/", "core.py", "README.md", "docs/", "(1 files below)"):
        assert shown in text, shown
    for hidden in ("ignored", "secret.py", "node_modules", "debug.log", "logo.png", ".git/"):
        assert hidden not in text, hidden
    sub = await call(env, "list_tree", path="pkg", depth=1)
    assert sub.output.startswith("pkg/ (3 files)")
    assert (await call(env, "list_tree", path="README.md")).error_code == "not_a_directory"


async def test_search_files(env: Env) -> None:
    result = await call(env, "search_files", glob="**/test_*.py")
    assert result.output == "tests/test_core.py"
    assert (await call(env, "search_files", glob="*.py")).data["matches"] == 4
    assert "no files match" in (await call(env, "search_files", glob="*.rs")).output


async def test_search_text(env: Env) -> None:
    result = await call(env, "search_text", pattern=r"def \w+\(")
    assert result.ok
    lines = result.output.splitlines()
    assert "pkg/core.py:15:def chunk(items: list[int], size: int) -> list[list[int]]:" in lines
    assert not any(line.startswith(("ignored/", "node_modules/", "debug.log")) for line in lines)
    capped = await call(env, "search_text", pattern="self", max_results=2)
    assert capped.data == {"matches": 2, "capped": True}
    assert (await call(env, "search_text", pattern="(unclosed")).error_code == "invalid_pattern"
    assert "no matches" in (await call(env, "search_text", pattern="zzz_nothing")).output
    literal = await call(env, "search_text", pattern="items[i", fixed_string=True)
    assert literal.data["matches"] == 1
    assert (await call(env, "search_text", pattern="x", path="../")).status is CallStatus.DENIED


async def test_find_symbol_and_references(env: Env) -> None:
    method = await call(env, "find_symbol", name="Wallet.withdraw")
    assert method.output.startswith("pkg/core.py:8: method Wallet.withdraw")
    bare = await call(env, "find_symbol", name="withdraw")
    assert bare.data["matches"] == 1
    const = await call(env, "find_symbol", name="LIMIT")
    assert "variable LIMIT" in const.output
    refs = await call(env, "find_references", name="chunk")
    locations = {(loc["path"], loc["kind"]) for loc in refs.data["locations"]}
    assert ("pkg/util.py", "import") in locations
    assert ("tests/test_core.py", "name") in locations
    kw = await call(env, "find_references", name="amount")
    assert any(loc["kind"] == "keyword" for loc in kw.data["locations"])
    assert (await call(env, "find_symbol", name="not valid!")).error_code == "invalid_arguments"
    assert "no definition" in (await call(env, "find_symbol", name="Nope")).output


async def test_ast_tools_skip_unparseable_files(env: Env) -> None:
    ctx = env[1]
    (ctx.repo / "broken.py").write_text("def chunk(:\n")
    (ctx.repo / "deep.py").write_text("x = " + "(" * 5000 + ")" * 5000 + "\n")
    result = await call(env, "find_symbol", name="chunk")
    assert result.ok
    assert result.data["matches"] == 1


# ------------------------------------------------------------------ git_diff
async def test_git_diff_includes_edits_and_new_files_not_toolchain_noise(env: Env) -> None:
    ctx = env[1]
    assert (await call(env, "git_diff")).output == "no changes"
    await call(env, "edit_file", path="pkg/core.py", old_str="LIMIT = 10", new_str="LIMIT = 20")
    await call(env, "create_file", path="tests/test_new.py", content="def test_new():\n    pass\n")
    (ctx.repo / "pkg.egg-info").mkdir()
    (ctx.repo / "pkg.egg-info" / "PKG-INFO").write_text("Name: pkg\n")
    (ctx.repo / "pkg" / "__pycache__").mkdir()
    (ctx.repo / "pkg" / "__pycache__" / "core.cpython-312.pyc").write_bytes(b"\x00")
    result = await call(env, "git_diff")
    assert result.data == {"files_changed": 2}
    assert "-LIMIT = 10\n+LIMIT = 20" in result.output
    assert "+++ b/tests/test_new.py" in result.output
    assert "egg-info" not in result.output
    assert "__pycache__" not in result.output


# ------------------------------------------------------------------ sandbox tools
async def test_run_command_goes_to_sandbox(env: Env) -> None:
    sandbox = env[3]
    result = await call(env, "run_command", argv=["python", "repro.py"])
    assert result.ok
    assert result.output.startswith("$ python repro.py\n[exit code 0, 0.2s]")
    assert sandbox.calls == [("run", ("python", "repro.py"))]


async def test_run_tests_reports_structured_results(env: Env) -> None:
    sandbox = env[3]
    result = await call(env, "run_tests", selector=["tests/test_core.py"])
    assert result.ok
    assert result.output.startswith("2 tests: 1 passed, 1 failed, 0 errors, 0 skipped")
    assert "FAILED: tests.test_core::test_chunk" in result.output
    assert result.data["report"]["failed"] == 1
    assert sandbox.calls == [("tests", ("python", "-m", "pytest", "tests/test_core.py"))]
    bad = await call(env, "run_tests", selector=["--rootdir=/"])
    assert bad.status is CallStatus.DENIED


async def test_sandbox_tools_without_sandbox(tmp_path: Path) -> None:
    workspace, base = await make_workspace(tmp_path)
    registry = default_registry()
    result = await registry.call("run_command", {"argv": ["ls"]}, context(workspace, base, None))
    assert result.error_code == "sandbox_unavailable"
