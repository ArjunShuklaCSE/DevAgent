"""SQLAlchemy models for every table in spec Section 9.

Enumerations are stored as VARCHAR with CHECK constraints (``native_enum=False``) so
adding a value is a plain migration rather than an ``ALTER TYPE`` (ADR 0008).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.events import EventType
from core.run_status import RunStatus
from core.tools import CallStatus, ChangeType, ToolCapability
from database.base import Base, IdMixin, TimestampMixin


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


# --------------------------------------------------------------------------- enums


class CredentialKind(StrEnum):
    OAUTH = "oauth"
    PAT = "pat"
    APP_INSTALLATION = "app_installation"


class RepositorySource(StrEnum):
    GITHUB = "github"
    LOCAL = "local"


class IssueSource(StrEnum):
    GITHUB = "github"
    PASTED = "pasted"


class RunMode(StrEnum):
    DRY_RUN = "dry_run"
    AGENT = "agent"


class StepStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TestRunKind(StrEnum):
    REPRODUCTION = "reproduction"
    SUITE = "suite"
    VALIDATION = "validation"
    HIDDEN = "hidden"


class TestOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class EvalRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureCategory(StrEnum):
    ENVIRONMENT = "environment"
    LOCALIZATION = "localization"
    REPRODUCTION = "reproduction"
    INCORRECT_FIX = "incorrect_fix"
    REGRESSION = "regression"
    INVALID_PATCH = "invalid_patch"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    INFRA = "infra"


# --------------------------------------------------------------------------- users & GitHub


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_url: Mapped[str | None] = mapped_column(String(500))


class GithubCredential(IdMixin, TimestampMixin, Base):
    """Tokens are Fernet-encrypted at rest (Phase 8); never stored in plaintext."""

    __tablename__ = "github_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[CredentialKind] = mapped_column(_enum(CredentialKind, "credential_kind"))
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[list[Any]] = mapped_column(default=list)
    expires_at: Mapped[datetime | None]

    __table_args__ = (Index(None, "user_id", "kind"),)


class Repository(IdMixin, TimestampMixin, Base):
    __tablename__ = "repositories"

    source: Mapped[RepositorySource] = mapped_column(_enum(RepositorySource, "repository_source"))
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    clone_url: Mapped[str] = mapped_column(String(500))
    default_branch: Mapped[str | None] = mapped_column(String(255))
    added_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (UniqueConstraint("source", "owner", "name"),)


class Issue(IdMixin, TimestampMixin, Base):
    __tablename__ = "issues"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[IssueSource] = mapped_column(_enum(IssueSource, "issue_source"))
    number: Mapped[int | None]
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(500))

    # Imported GitHub issues are unique per repo; pasted issues have no number.
    __table_args__ = (UniqueConstraint("repository_id", "number"),)


# --------------------------------------------------------------------------- runs


class AgentRun(IdMixin, TimestampMixin, Base):
    """One attempt to solve one issue. Stores everything needed to replay it (spec 9)."""

    __tablename__ = "agent_runs"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    mode: Mapped[RunMode] = mapped_column(_enum(RunMode, "run_mode"))
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)

    # Replayability
    base_commit_sha: Mapped[str | None] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sandbox_image_digest: Mapped[str | None] = mapped_column(String(100))
    final_diff: Mapped[str | None] = mapped_column(Text)
    final_diff_sha256: Mapped[str | None] = mapped_column(String(64))

    # Accounting
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    fix_attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    injection_flags: Mapped[list[Any]] = mapped_column(default=list)
    trace_id: Mapped[str | None] = mapped_column(String(32))

    # Event stream bookkeeping: incremented atomically when an event is appended.
    last_event_seq: Mapped[int] = mapped_column(BigInteger, default=0)

    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    issue: Mapped[Issue] = relationship(lazy="raise")
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", order_by="AgentStep.sequence", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(None, "created_at"),
        CheckConstraint("last_event_seq >= 0", name="event_seq_non_negative"),
    )


class AgentStep(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int]
    state: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "step_state"))
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"))
    rationale: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[dict[str, Any] | None]
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]

    run: Mapped[AgentRun] = relationship(back_populates="steps")

    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class RunEvent(IdMixin, TimestampMixin, Base):
    """Ordered, append-only event log per run; ``seq`` is the SSE event id."""

    __tablename__ = "run_events"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(BigInteger)
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL")
    )
    event_type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"))
    payload: Mapped[dict[str, Any]]

    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        CheckConstraint("seq > 0", name="seq_positive"),
    )


class ToolCallRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    capability: Mapped[ToolCapability] = mapped_column(_enum(ToolCapability, "tool_capability"))
    input: Mapped[dict[str, Any]]
    output_truncated: Mapped[str | None] = mapped_column(Text)
    output_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_full_ref: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[CallStatus] = mapped_column(_enum(CallStatus, "tool_call_status"))
    error: Mapped[dict[str, Any] | None]
    duration_ms: Mapped[int]


class LlmCall(IdMixin, TimestampMixin, Base):
    __tablename__ = "llm_calls"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    component: Mapped[str] = mapped_column(String(50))
    prompt_version: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int]
    status: Mapped[CallStatus] = mapped_column(_enum(CallStatus, "llm_call_status"))
    rationale: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None]


class CodeChange(IdMixin, TimestampMixin, Base):
    __tablename__ = "code_changes"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL")
    )
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="SET NULL")
    )
    path: Mapped[str] = mapped_column(String(1000))
    change_type: Mapped[ChangeType] = mapped_column(_enum(ChangeType, "change_type"))
    before_sha256: Mapped[str | None] = mapped_column(String(64))
    after_sha256: Mapped[str | None] = mapped_column(String(64))
    diff: Mapped[str] = mapped_column(Text)
    is_sensitive_path: Mapped[bool] = mapped_column(Boolean, default=False)


class TestRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "test_runs"
    __test__ = False  # not a pytest test class

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL")
    )
    kind: Mapped[TestRunKind] = mapped_column(_enum(TestRunKind, "test_run_kind"))
    command: Mapped[list[Any]]
    exit_code: Mapped[int | None]
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int]
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)

    results: Mapped[list["TestResult"]] = relationship(
        back_populates="test_run", cascade="all, delete-orphan"
    )


class TestResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "test_results"
    __test__ = False

    test_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(1000))
    outcome: Mapped[TestOutcome] = mapped_column(_enum(TestOutcome, "test_outcome"))
    duration_ms: Mapped[int | None]
    message: Mapped[str | None] = mapped_column(Text)

    test_run: Mapped[TestRun] = relationship(back_populates="results")


class Approval(IdMixin, TimestampMixin, Base):
    """A human decision bound to the exact diff hash it was made on (spec 2.7)."""

    __tablename__ = "approvals"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[ApprovalDecision] = mapped_column(_enum(ApprovalDecision, "approval_decision"))
    diff_sha256: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)


class PullRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), unique=True
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int]
    url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(255))
    head_sha: Mapped[str] = mapped_column(String(40))
    state: Mapped[PullRequestState] = mapped_column(_enum(PullRequestState, "pr_state"))
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- evaluation


class EvalDataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "eval_datasets"

    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(String(500))
    content_sha256: Mapped[str] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("name", "version"),)


class EvalCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "eval_cases"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE")
    )
    case_key: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(50))
    framework: Mapped[str] = mapped_column(String(50))
    difficulty: Mapped[str] = mapped_column(String(20))
    spec: Mapped[dict[str, Any]]

    __table_args__ = (UniqueConstraint("dataset_id", "case_key"),)


class EvalRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "eval_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[EvalRunStatus] = mapped_column(_enum(EvalRunStatus, "eval_run_status"))
    model: Mapped[str] = mapped_column(String(100))
    label: Mapped[str | None] = mapped_column(String(200))
    config: Mapped[dict[str, Any]]
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    repeats: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class EvalResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "eval_results"

    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"))
    eval_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_cases.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    repeat_index: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean)
    patch_applied: Mapped[bool] = mapped_column(Boolean)
    reproduction_created: Mapped[bool] = mapped_column(Boolean)
    fail_to_pass_passed: Mapped[int] = mapped_column(Integer, default=0)
    fail_to_pass_total: Mapped[int] = mapped_column(Integer, default=0)
    pass_to_pass_regressions: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    wall_clock_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    failure_category: Mapped[FailureCategory | None] = mapped_column(
        _enum(FailureCategory, "failure_category")
    )

    __table_args__ = (UniqueConstraint("eval_run_id", "eval_case_id", "repeat_index"),)
