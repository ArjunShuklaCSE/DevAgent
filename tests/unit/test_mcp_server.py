"""The MCP server, driven through a real MCP client connected in-process."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client, StdioServerParameters
from pydantic import SecretStr

from mcp_server.api_client import DevAgentApi
from mcp_server.server import DevAgentMcp

RUN_ID = "11111111-2222-3333-4444-555555555555"
REPO_ID = "66666666-7777-8888-9999-000000000000"


class FakeApi:
    """The DevAgent REST API, recorded: answers what the MCP tools ask for."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if (method, path) == ("GET", "/api/v1/runs"):
            return httpx.Response(200, json={"items": [], "total": 0, "limit": 5, "offset": 0})
        if (method, path) == ("POST", "/api/v1/runs"):
            return httpx.Response(201, json={"id": RUN_ID, "status": "queued"})
        if (method, path) == ("POST", f"/api/v1/runs/{RUN_ID}/cancel"):
            return httpx.Response(200, json={"id": RUN_ID, "status": "cancelled"})
        if (method, path) == ("GET", f"/api/v1/runs/{RUN_ID}/trace"):
            return httpx.Response(200, json={"format": "devagent-trace/v1", "steps": []})
        return httpx.Response(
            404, json={"error": {"code": "not_found", "message": "Run not found", "details": {}}}
        )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def answer() -> int:\n    return 42\n")
    (tmp_path / "secret.txt").write_text("outside the checkout")
    # Search tools list files through git, as in a run's workspace.
    for argv in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", *argv], cwd=root, check=True)
    return root


def _server(checkout: Path | None, fake: FakeApi | None) -> DevAgentMcp:
    api = (
        DevAgentApi(
            "http://devagent.test",
            session_cookie=SecretStr("sealed-session"),
            transport=httpx.MockTransport(fake),
        )
        if fake is not None
        else None
    )
    return DevAgentMcp(api, checkout)


def _text(result: Any) -> str:
    return "".join(part.text for part in result.content)


async def test_lists_only_read_tools_and_run_control(checkout: Path) -> None:
    async with Client(_server(checkout, FakeApi()).server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    code = {"list_tree", "search_files", "search_text", "find_symbol", "find_references"}
    assert code <= set(tools)
    assert {"read_file", "start_run", "cancel_run", "export_run_trace"} <= set(tools)
    # Nothing that writes files (git_diff stages into the index), runs code, or ships.
    for forbidden in ("edit_file", "create_file", "run_command", "run_tests", "git_diff"):
        assert forbidden not in tools
    assert not any("approve" in name or "publish" in name for name in tools)
    assert tools["read_file"].annotations is not None
    assert tools["read_file"].annotations.read_only_hint
    assert tools["cancel_run"].annotations is not None
    assert tools["cancel_run"].annotations.destructive_hint


def _git_state(root: Path) -> tuple[bytes, bytes]:
    exclude = root / ".git" / "info" / "exclude"
    return (root / ".git" / "index").read_bytes(), exclude.read_bytes()


async def test_code_tools_read_the_checkout_and_refuse_escapes(checkout: Path) -> None:
    (checkout / "notes.txt").write_text("untracked")
    before = _git_state(checkout)
    async with Client(_server(checkout, None).server()) as client:
        tree = await client.call_tool("list_tree", {"path": ".", "depth": 3})
        search = await client.call_tool("search_text", {"pattern": "untracked"})
        found = await client.call_tool("find_symbol", {"name": "answer"})
        read = await client.call_tool(
            "read_file", {"path": "pkg/core.py", "start_line": 1, "end_line": 2}
        )
        escape = await client.call_tool(
            "read_file", {"path": "../secret.txt", "start_line": 1, "end_line": 1}
        )
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not found.is_error
    assert "pkg/core.py" in _text(found)
    assert "notes.txt" in _text(tree)
    assert "notes.txt" in _text(search)
    assert _git_state(checkout) == before  # reading never touches the user's .git
    assert "return 42" in _text(read)
    assert escape.is_error
    assert "outside the checkout" not in _text(escape)
    assert "start_run" not in names  # no API configured


async def test_run_control_goes_through_the_api(checkout: Path) -> None:
    fake = FakeApi()
    async with Client(_server(None, fake).server()) as client:
        started = await client.call_tool(
            "start_run",
            {
                "repository_id": REPO_ID,
                "issue_title": "slugify crashes",
                "issue_number": 3,
                "max_cost_usd": 0.5,
                "max_fix_attempts": 1,
            },
        )
        cancelled = await client.call_tool("cancel_run", {"run_id": RUN_ID})
        trace = await client.call_tool("export_run_trace", {"run_id": RUN_ID})
        missing = await client.call_tool("get_run", {"run_id": REPO_ID})
        invalid = await client.call_tool("get_run", {"run_id": "not-a-uuid"})
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert json.loads(_text(started))["status"] == "queued"
    body = json.loads(fake.requests[0].content)
    assert body["issue"] == {"title": "slugify crashes", "body": "", "number": 3}
    assert body["budget"] == {"max_cost_usd": "0.5", "max_fix_attempts": 1}
    assert "devagent_session=sealed-session" in fake.requests[0].headers["cookie"]
    assert json.loads(_text(cancelled))["status"] == "cancelled"
    assert json.loads(_text(trace))["format"] == "devagent-trace/v1"
    assert missing.is_error
    assert json.loads(_text(missing))["error"]["code"] == "not_found"
    assert invalid.is_error
    assert "list_tree" not in names  # no checkout configured
    assert len(fake.requests) == 4  # the invalid call never reached the API


async def test_unreachable_api_is_a_tool_error_not_a_crash() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api = DevAgentApi("http://devagent.test", transport=httpx.MockTransport(refuse))
    async with Client(DevAgentMcp(api, None).server()) as client:
        result = await client.call_tool("list_runs", {})
    assert result.is_error
    assert json.loads(_text(result))["error"]["code"] == "api_unreachable"


async def test_stdio_entrypoint_serves_a_real_client(checkout: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server", "--root", str(checkout), "--no-runs"],
        cwd=str(Path(__file__).parents[2]),
    )
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        tree = await client.call_tool("list_tree", {"path": ".", "depth": 2})
    assert "start_run" not in names
    assert "pkg/core.py" in _text(tree) or "core.py" in _text(tree)
