"""git_diff: the current change set against the run's base commit."""

from typing import ClassVar

from core.tools import ToolCapability
from tools.base import BaseTool, ToolContext, ToolInput, ToolOutput
from tools.git import diff_against_base


class GitDiffInput(ToolInput):
    pass


class GitDiffTool(BaseTool[GitDiffInput]):
    name: ClassVar[str] = "git_diff"
    description: ClassVar[str] = (
        "Show all changes made so far, as a unified diff against the base commit."
    )
    capability: ClassVar[ToolCapability] = ToolCapability.READ
    input_model = GitDiffInput
    max_output_chars: ClassVar[int] = 60_000

    async def run(self, args: GitDiffInput, ctx: ToolContext) -> ToolOutput:
        diff, stat = await diff_against_base(ctx.repo, ctx.base_commit)
        if not diff.strip():
            return ToolOutput(text="no changes", data={"files_changed": 0})
        files = sum(1 for line in diff.splitlines() if line.startswith("diff --git "))
        return ToolOutput(text=f"{stat.rstrip()}\n\n{diff}", data={"files_changed": files})
