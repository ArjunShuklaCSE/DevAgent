"""Repository profile produced by static analysis (spec 4.4, Repository Analyzer)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Command = list[str]


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    claim: str
    source: str  # file path relative to the repo root, or "tree" for layout-based facts


class RepoProfile(BaseModel):
    """Everything later steps need to build, test and validate the repository.

    Commands are argv lists (never shell strings) and are executed only in the sandbox.
    """

    languages: dict[str, int] = Field(description="file count per language")
    primary_language: str | None
    adapter: str | None = Field(description="language adapter that produced the commands")
    supported: bool = Field(description="True if v1 can run this repository end to end")
    unsupported_reason: str | None = None
    framework: str | None = Field(description="application framework, e.g. django")
    package_manager: str | None
    dependency_files: list[str]
    test_framework: str | None
    install_commands: list[Command]
    test_command: Command | None
    lint_command: Command | None
    format_check_command: Command | None
    typecheck_command: Command | None
    entry_points: list[str]
    key_directories: list[str]
    test_directories: list[str]
    evidence: list[Evidence]
    confidence: Literal["high", "medium", "low"]
