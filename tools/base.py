"""Tool contract: typed inputs, a capability tag, limits, and a structured result."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict

from core.tools import ChangeType, ToolCapability
from sandbox.docker_sandbox import CommandResult, RunWorkspace, TestRun
from sandbox.policy import Profile


class ToolInput(BaseModel):
    """Base for tool argument models: unknown arguments are an error, not ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class FileChange:
    path: str
    change_type: ChangeType
    before_sha256: str | None
    after_sha256: str | None
    diff: str
    path_class: str  # tools.sensitive.PathClass

    @property
    def is_sensitive(self) -> bool:
        return self.path_class != "normal"


@dataclass(frozen=True)
class ToolOutput:
    """What a tool returns: ``text`` goes to the model, ``data`` is for the UI and logs."""

    text: str
    data: dict[str, Any] = field(default_factory=dict)
    changes: tuple[FileChange, ...] = ()


class SandboxRunner(Protocol):
    """The part of ``DockerSandbox`` that tools use (fakes implement it in tests)."""

    async def run(
        self,
        workspace: RunWorkspace,
        argv: Sequence[str],
        *,
        profile: Profile = "run",
        timeout_seconds: float | None = None,
    ) -> CommandResult: ...

    async def run_tests(
        self,
        workspace: RunWorkspace,
        test_command: Sequence[str],
        extra_args: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> TestRun: ...


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    workspace: RunWorkspace
    base_commit: str
    sandbox: SandboxRunner | None = None
    test_command: tuple[str, ...] | None = None
    # Protected paths (CI config, lockfiles) the approved plan explicitly justified.
    allowed_protected_paths: frozenset[str] = frozenset()
    # Paths no tool may write in this step (e.g. the confirmed reproduction test).
    read_only_paths: frozenset[str] = frozenset()
    # Upper bound for any sandbox command in this run (the run budget's command timeout).
    command_timeout_seconds: float | None = None
    step_id: str | None = None

    @property
    def repo(self) -> Path:
        return self.workspace.repo


class BaseTool[InputT: ToolInput](ABC):
    """A tool. Subclasses set the class attributes and implement ``run``."""

    name: ClassVar[str]
    description: ClassVar[str]
    capability: ClassVar[ToolCapability]
    input_model: type[InputT]
    max_output_chars: ClassVar[int] = 12_000

    def parse(self, arguments: dict[str, Any]) -> InputT:
        return self.input_model.model_validate(arguments)

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema

    @abstractmethod
    async def run(self, args: InputT, ctx: ToolContext) -> ToolOutput:
        """Do the work. Raise ``ToolError`` for expected failures."""
