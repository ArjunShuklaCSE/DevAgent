"""The standard tool set (spec 5)."""

from typing import Any

from tools.base import BaseTool
from tools.diff import GitDiffTool
from tools.execution import RunCommandTool, RunTestsTool
from tools.files import CreateFileTool, EditFileTool, ListTreeTool, ReadFileTool, SearchFilesTool
from tools.registry import ToolCallSink, ToolRegistry
from tools.search import FindReferencesTool, FindSymbolTool, SearchTextTool


def default_tools() -> list[BaseTool[Any]]:
    return [
        ListTreeTool(),
        SearchFilesTool(),
        SearchTextTool(),
        FindSymbolTool(),
        FindReferencesTool(),
        ReadFileTool(),
        EditFileTool(),
        CreateFileTool(),
        RunCommandTool(),
        RunTestsTool(),
        GitDiffTool(),
    ]


def default_registry(sink: ToolCallSink | None = None) -> ToolRegistry:
    return ToolRegistry(default_tools(), sink)
