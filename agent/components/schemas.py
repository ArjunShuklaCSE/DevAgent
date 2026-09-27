"""Structured outputs of the LLM-backed components (spec 4.4).

Every model extends ``StructuredOutput``, so each answer carries a short rationale that
is stored and shown. Field limits keep answers small and make off-task output fail
validation (and be retried) instead of flowing into later steps.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm.structured import StructuredOutput

Confidence = Literal["high", "medium", "low"]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueAnalysis(StructuredOutput):
    summary: str = Field(min_length=1, max_length=500)
    expected_behavior: str = Field(min_length=1, max_length=1000)
    current_behavior: str = Field(min_length=1, max_length=1000)
    reproduction_steps: list[str] = Field(default_factory=list, max_length=10)
    suspected_areas: list[str] = Field(
        default_factory=list, max_length=10, description="files, modules or functions"
    )
    acceptance_criteria: list[str] = Field(min_length=1, max_length=10)
    confidence: Confidence


class Candidate(_Item):
    path: str = Field(min_length=1, max_length=500)
    symbol: str | None = Field(default=None, max_length=200)
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(
        min_length=1, max_length=5, description="search hits, tracebacks or imports"
    )


class Localization(StructuredOutput):
    candidates: list[Candidate] = Field(min_length=1, max_length=8)


class Reproduction(StructuredOutput):
    test_file: str = Field(min_length=1, max_length=500, description="the new test file")
    test_ids: list[str] = Field(
        min_length=1,
        max_length=10,
        description="pytest node ids that fail because of the bug, e.g. tests/test_x.py::test_y",
    )
    explanation: str = Field(min_length=1, max_length=800, description="why the test fails")


class ProtectedPathChange(_Item):
    path: str = Field(min_length=1, max_length=500)
    justification: str = Field(min_length=10, max_length=500)


class Plan(StructuredOutput):
    steps: list[str] = Field(min_length=1, max_length=10)
    files_to_touch: list[str] = Field(min_length=1, max_length=10)
    risks: list[str] = Field(default_factory=list, max_length=5)
    test_strategy: str = Field(min_length=1, max_length=800)
    protected_path_changes: list[ProtectedPathChange] = Field(
        default_factory=list,
        max_length=3,
        description="CI config or lockfiles the fix must change, each with a reason; "
        "normally empty",
    )


class EditReport(StructuredOutput):
    changes_made: list[str] = Field(min_length=1, max_length=10)


FailureClass = Literal["assertion", "import_error", "syntax", "env_dependency", "timeout", "flaky"]


class DebugDiagnosis(StructuredOutput):
    failure_class: FailureClass
    root_cause: str = Field(min_length=1, max_length=800)
    next_action: Literal["retry_edit", "give_up"]
    guidance: str = Field(
        min_length=1, max_length=1500, description="what the next edit must change"
    )


class PullRequestText(StructuredOutput):
    title: str = Field(min_length=5, max_length=72)
    issue_summary: str = Field(min_length=1, max_length=1000)
    root_cause: str = Field(min_length=1, max_length=1500)
    changes_made: list[str] = Field(min_length=1, max_length=10)
    tests_added: list[str] = Field(min_length=1, max_length=10)
    commit_message: str = Field(min_length=5, max_length=500)
