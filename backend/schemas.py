"""API request/response models (``/api/v1``)."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    """A GitHub repository (``url``) or one of the bundled sample repositories (``sample``)."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, examples=["https://github.com/pallets/click"])
    sample: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$", examples=["slugger"]
    )

    @field_validator("url")
    @classmethod
    def _must_be_github_repo_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _GITHUB_URL.match(value.strip()):
            raise ValueError("must be a GitHub repository URL like https://github.com/owner/name")
        return value.strip()

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "RepositoryCreate":
        if (self.url is None) == (self.sample is None):
            raise ValueError("give exactly one of 'url' or 'sample'")
        return self

    def owner_and_name(self) -> tuple[str, str]:
        match = _GITHUB_URL.match(self.url or "")
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
    number: int | None = Field(default=None, ge=1, description="GitHub issue number, if any")


class RunBudget(BaseModel):
    """Per-run limits (spec 6.6), stored in the run's config snapshot and enforced by the
    agent before every step, fix attempt and LLM call."""

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
    # "agent" runs the real agent; "dry_run" walks the state machine with synthetic steps.
    mode: RunMode = RunMode.AGENT
    model: str | None = Field(
        default=None, max_length=100, description="model from the pricing file; default: server"
    )
    budget: RunBudget = Field(default_factory=RunBudget)
    dry_run_step_delay_ms: int = Field(default=250, ge=0, le=10_000)


class IssueOut(_Out):
    id: UUID
    source: IssueSource
    number: int | None
    title: str
    body: str


class RepositoryBrief(_Out):
    id: UUID
    source: RepositorySource
    owner: str
    name: str


class IssueBrief(_Out):
    number: int | None
    title: str


class RunOut(_Out):
    id: UUID
    repository_id: UUID
    repository: RepositoryBrief
    issue: IssueOut
    mode: RunMode
    status: RunStatus
    status_reason: str | None
    config: dict[str, Any]
    base_commit_sha: str | None
    sandbox_image_digest: str | None
    prompt_versions: dict[str, Any]
    final_diff_sha256: str | None
    injection_flags: list[Any]
    result: dict[str, Any]
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
    repository: RepositoryBrief
    issue: IssueBrief
    mode: RunMode
    status: RunStatus
    status_reason: str | None
    step_count: int
    fix_attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    created_at: datetime
    started_at: datetime | None
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


# --------------------------------------------------------------------------- run details


class ToolCallOut(_Out):
    id: UUID
    step_id: UUID | None
    tool_name: str
    capability: str
    input: dict[str, Any]
    output_truncated: str | None
    output_size_bytes: int
    status: str
    error: dict[str, Any] | None
    duration_ms: int
    created_at: datetime


class LlmCallOut(_Out):
    id: UUID
    step_id: UUID | None
    provider: str
    model: str
    component: str
    prompt_version: str
    attempt: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    status: str
    rationale: str | None
    error: dict[str, Any] | None
    created_at: datetime


class TestResultOut(_Out):
    __test__ = False

    node_id: str
    outcome: str
    duration_ms: int | None
    message: str | None


class TestRunOut(_Out):
    __test__ = False

    id: UUID
    step_id: UUID | None
    kind: str
    command: list[Any]
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    passed: int
    failed: int
    errors: int
    skipped: int
    created_at: datetime
    results: list[TestResultOut]


class DiffOut(BaseModel):
    run_id: UUID
    diff: str | None
    sha256: str | None
    review_flags: list[dict[str, Any]]
    validation: dict[str, Any] | None


class ApprovalRequest(BaseModel):
    """Approve exactly the diff the reviewer saw: its SHA-256 must match the run's."""

    model_config = ConfigDict(extra="forbid")

    diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=4000)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=4000)


class ApprovalOut(_Out):
    id: UUID
    decision: str
    diff_sha256: str
    comment: str | None
    created_at: datetime


# --------------------------------------------------------------------------- auth & GitHub


class UserOut(_Out):
    id: UUID
    login: str
    name: str | None
    avatar_url: str | None


class AuthStatus(BaseModel):
    oauth_enabled: bool = Field(description="GitHub sign-in is configured on this server")
    github_token_configured: bool = Field(
        description="A server-wide token (DEVAGENT_GITHUB_TOKEN) is available for GitHub calls"
    )
    user: UserOut | None


class GitHubIssueOut(BaseModel):
    number: int
    title: str
    body: str
    html_url: str
    labels: list[str]
    comments: int
    user: str | None
    created_at: str


class PullRequestOut(_Out):
    number: int
    url: str
    branch: str
    head_sha: str
    state: str
    is_draft: bool
    created_at: datetime
