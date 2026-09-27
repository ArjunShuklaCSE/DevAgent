"""API request/response models (``/api/v1``)."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.run_status import RunStatus
from database.models import IssueSource, RepositorySource, RunMode, StepStatus

_GITHUB_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- repositories


class RepositoryCreate(BaseModel):
    url: str = Field(examples=["https://github.com/pallets/click"])

    @field_validator("url")
    @classmethod
    def _must_be_github_repo_url(cls, value: str) -> str:
        if not _GITHUB_URL.match(value.strip()):
            raise ValueError("must be a GitHub repository URL like https://github.com/owner/name")
        return value.strip()

    def owner_and_name(self) -> tuple[str, str]:
        match = _GITHUB_URL.match(self.url)
        assert match is not None  # noqa: S101 - validated above
        return match["owner"], match["name"]


class RepositoryOut(_Out):
    id: UUID
    source: RepositorySource
    owner: str
    name: str
    clone_url: str
    default_branch: str | None
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- runs


class IssueInput(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=65_536)


class RunBudget(BaseModel):
    """Per-run limits (spec 6.6). Stored in the run's config snapshot; enforced from Phase 5."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=40, ge=1, le=500)
    max_fix_attempts: int = Field(default=3, ge=0, le=20)
    max_tokens: int = Field(default=400_000, ge=1_000, le=10_000_000)
    max_cost_usd: Decimal = Field(default=Decimal("2.00"), gt=0, le=100)
    command_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    wall_clock_seconds: int = Field(default=1800, ge=10, le=6 * 3600)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    issue: IssueInput
    # Only the synthetic dry run exists until the agent loop lands (Phase 6).
    mode: Literal[RunMode.DRY_RUN] = RunMode.DRY_RUN
    budget: RunBudget = Field(default_factory=RunBudget)
    dry_run_step_delay_ms: int = Field(default=250, ge=0, le=10_000)


class IssueOut(_Out):
    id: UUID
    source: IssueSource
    number: int | None
    title: str
    body: str


class RunOut(_Out):
    id: UUID
    repository_id: UUID
    issue: IssueOut
    mode: RunMode
    status: RunStatus
    status_reason: str | None
    config: dict[str, Any]
    step_count: int
    fix_attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    last_event_seq: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunSummary(_Out):
    id: UUID
    repository_id: UUID
    mode: RunMode
    status: RunStatus
    step_count: int
    cost_usd: Decimal
    created_at: datetime
    finished_at: datetime | None


class StepOut(_Out):
    id: UUID
    sequence: int
    state: RunStatus
    status: StepStatus
    synthetic: bool
    summary: str | None
    rationale: str | None
    output: dict[str, Any]
    error: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class EventOut(BaseModel):
    """The JSON ``data`` of every SSE message. ``seq`` is also the SSE ``id``."""

    seq: int
    run_id: UUID
    step_id: UUID | None
    type: str
    created_at: datetime
    payload: dict[str, Any]
