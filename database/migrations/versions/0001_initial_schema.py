"""Initial schema: every table from spec Section 9.

Revision ID: 0001
Revises:
Create Date: 2026-09-27 09:27:14.625519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_datasets")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_eval_datasets_name_version")),
    )
    op.create_table(
        "users",
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("github_id", name=op.f("uq_users_github_id")),
    )
    op.create_index(op.f("ix_users_login"), "users", ["login"], unique=False)
    op.create_table(
        "eval_cases",
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("case_key", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("framework", sa.String(length=50), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name=op.f("fk_eval_cases_dataset_id_eval_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_cases")),
        sa.UniqueConstraint(
            "dataset_id", "case_key", name=op.f("uq_eval_cases_dataset_id_case_key")
        ),
    )
    op.create_table(
        "eval_runs",
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "failed",
                name="eval_run_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_eval_runs_eval_run_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name=op.f("fk_eval_runs_dataset_id_eval_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )
    op.create_index(op.f("ix_eval_runs_dataset_id"), "eval_runs", ["dataset_id"], unique=False)
    op.create_table(
        "github_credentials",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "oauth",
                "pat",
                "app_installation",
                name="credential_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('oauth', 'pat', 'app_installation')",
            name=op.f("ck_github_credentials_credential_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_github_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_credentials")),
    )
    op.create_index(
        op.f("ix_github_credentials_user_id"),
        "github_credentials",
        ["user_id", "kind"],
        unique=False,
    )
    op.create_table(
        "repositories",
        sa.Column(
            "source",
            sa.Enum(
                "github",
                "local",
                name="repository_source",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("clone_url", sa.String(length=500), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        sa.Column("added_by_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('github', 'local')", name=op.f("ck_repositories_repository_source")
        ),
        sa.ForeignKeyConstraint(
            ["added_by_id"],
            ["users.id"],
            name=op.f("fk_repositories_added_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint(
            "source", "owner", "name", name=op.f("uq_repositories_source_owner_name")
        ),
    )
    op.create_table(
        "issues",
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "github",
                "pasted",
                name="issue_source",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source IN ('github', 'pasted')", name=op.f("ck_issues_issue_source")),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_issues_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issues")),
        sa.UniqueConstraint("repository_id", "number", name=op.f("uq_issues_repository_id_number")),
    )
    op.create_index(op.f("ix_issues_repository_id"), "issues", ["repository_id"], unique=False)
    op.create_table(
        "agent_runs",
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "mode",
            sa.Enum(
                "dry_run",
                "agent",
                name="run_mode",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "cloning",
                "analyzing_repo",
                "analyzing_issue",
                "localizing",
                "reproducing",
                "planning",
                "editing",
                "testing",
                "debugging",
                "validating",
                "awaiting_approval",
                "approved",
                "creating_pr",
                "pr_created",
                "rejected",
                "failed",
                "budget_exceeded",
                "cancelled",
                "timed_out",
                name="run_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("base_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sandbox_image_digest", sa.String(length=100), nullable=True),
        sa.Column("final_diff", sa.Text(), nullable=True),
        sa.Column("final_diff_sha256", sa.String(length=64), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("fix_attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("injection_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("last_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('dry_run', 'agent')", name=op.f("ck_agent_runs_run_mode")),
        sa.CheckConstraint(
            "status IN ('queued', 'cloning', 'analyzing_repo', 'analyzing_issue', 'localizing', 'reproducing', 'planning', 'editing', 'testing', 'debugging', 'validating', 'awaiting_approval', 'approved', 'creating_pr', 'pr_created', 'rejected', 'failed', 'budget_exceeded', 'cancelled', 'timed_out')",
            name=op.f("ck_agent_runs_run_status"),
        ),
        sa.CheckConstraint(
            "last_event_seq >= 0", name=op.f("ck_agent_runs_event_seq_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issues.id"],
            name=op.f("fk_agent_runs_issue_id_issues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_agent_runs_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_agent_runs_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_created_at"), "agent_runs", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_agent_runs_repository_id"), "agent_runs", ["repository_id"], unique=False
    )
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False)
    op.create_table(
        "agent_steps",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "queued",
                "cloning",
                "analyzing_repo",
                "analyzing_issue",
                "localizing",
                "reproducing",
                "planning",
                "editing",
                "testing",
                "debugging",
                "validating",
                "awaiting_approval",
                "approved",
                "creating_pr",
                "pr_created",
                "rejected",
                "failed",
                "budget_exceeded",
                "cancelled",
                "timed_out",
                name="step_state",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "failed",
                "skipped",
                name="step_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'cloning', 'analyzing_repo', 'analyzing_issue', 'localizing', 'reproducing', 'planning', 'editing', 'testing', 'debugging', 'validating', 'awaiting_approval', 'approved', 'creating_pr', 'pr_created', 'rejected', 'failed', 'budget_exceeded', 'cancelled', 'timed_out')",
            name=op.f("ck_agent_steps_step_state"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name=op.f("ck_agent_steps_step_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_steps_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_steps")),
        sa.UniqueConstraint("run_id", "sequence", name=op.f("uq_agent_steps_run_id_sequence")),
    )
    op.create_table(
        "approvals",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "approved",
                "rejected",
                name="approval_decision",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("diff_sha256", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name=op.f("ck_approvals_approval_decision")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_approvals_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_approvals_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(op.f("ix_approvals_run_id"), "approvals", ["run_id"], unique=False)
    op.create_table(
        "eval_results",
        sa.Column("eval_run_id", sa.UUID(), nullable=False),
        sa.Column("eval_case_id", sa.UUID(), nullable=False),
        sa.Column("agent_run_id", sa.UUID(), nullable=True),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("patch_applied", sa.Boolean(), nullable=False),
        sa.Column("reproduction_created", sa.Boolean(), nullable=False),
        sa.Column("fail_to_pass_passed", sa.Integer(), nullable=False),
        sa.Column("fail_to_pass_total", sa.Integer(), nullable=False),
        sa.Column("pass_to_pass_regressions", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("wall_clock_ms", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column(
            "failure_category",
            sa.Enum(
                "environment",
                "localization",
                "reproduction",
                "incorrect_fix",
                "regression",
                "invalid_patch",
                "budget_exceeded",
                "timeout",
                "infra",
                name="failure_category",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failure_category IN ('environment', 'localization', 'reproduction', 'incorrect_fix', 'regression', 'invalid_patch', 'budget_exceeded', 'timeout', 'infra')",
            name=op.f("ck_eval_results_failure_category"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_eval_results_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id"],
            ["eval_cases.id"],
            name=op.f("fk_eval_results_eval_case_id_eval_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_runs.id"],
            name=op.f("fk_eval_results_eval_run_id_eval_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_results")),
        sa.UniqueConstraint(
            "eval_run_id",
            "eval_case_id",
            "repeat_index",
            name=op.f("uq_eval_results_eval_run_id_eval_case_id_repeat_index"),
        ),
    )
    op.create_index(
        op.f("ix_eval_results_eval_case_id"), "eval_results", ["eval_case_id"], unique=False
    )
    op.create_table(
        "pull_requests",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "open",
                "closed",
                "merged",
                name="pr_state",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('open', 'closed', 'merged')", name=op.f("ck_pull_requests_pr_state")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_pull_requests_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_pull_requests_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pull_requests")),
        sa.UniqueConstraint("run_id", name=op.f("uq_pull_requests_run_id")),
    )
    op.create_index(
        op.f("ix_pull_requests_repository_id"), "pull_requests", ["repository_id"], unique=False
    )
    op.create_table(
        "llm_calls",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("component", sa.String(length=50), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "error",
                "denied",
                name="llm_call_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'denied')", name=op.f("ck_llm_calls_llm_call_status")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_llm_calls_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["agent_steps.id"],
            name=op.f("fk_llm_calls_step_id_agent_steps"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_table(
        "run_events",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "step_started",
                "step_completed",
                "tool_call",
                "command_output",
                "test_result",
                "llm_usage",
                "status_changed",
                "error",
                name="event_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('step_started', 'step_completed', 'tool_call', 'command_output', 'test_result', 'llm_usage', 'status_changed', 'error')",
            name=op.f("ck_run_events_event_type"),
        ),
        sa.CheckConstraint("seq > 0", name=op.f("ck_run_events_seq_positive")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_run_events_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["agent_steps.id"],
            name=op.f("fk_run_events_step_id_agent_steps"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("run_id", "seq", name=op.f("uq_run_events_run_id_seq")),
    )
    op.create_table(
        "test_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "reproduction",
                "suite",
                "validation",
                "hidden",
                name="test_run_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('reproduction', 'suite', 'validation', 'hidden')",
            name=op.f("ck_test_runs_test_run_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_test_runs_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["agent_steps.id"],
            name=op.f("fk_test_runs_step_id_agent_steps"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_runs")),
    )
    op.create_index(op.f("ix_test_runs_run_id"), "test_runs", ["run_id"], unique=False)
    op.create_table(
        "tool_calls",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column(
            "capability",
            sa.Enum(
                "read",
                "write_workspace",
                "execute_sandbox",
                name="tool_capability",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_truncated", sa.Text(), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_full_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "error",
                "denied",
                name="tool_call_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability IN ('read', 'write_workspace', 'execute_sandbox')",
            name=op.f("ck_tool_calls_tool_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'denied')", name=op.f("ck_tool_calls_tool_call_status")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["agent_steps.id"],
            name=op.f("fk_tool_calls_step_id_agent_steps"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    op.create_index(op.f("ix_tool_calls_run_id"), "tool_calls", ["run_id"], unique=False)
    op.create_index(op.f("ix_tool_calls_step_id"), "tool_calls", ["step_id"], unique=False)
    op.create_table(
        "code_changes",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column("tool_call_id", sa.UUID(), nullable=True),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column(
            "change_type",
            sa.Enum(
                "create",
                "modify",
                "delete",
                name="change_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("before_sha256", sa.String(length=64), nullable=True),
        sa.Column("after_sha256", sa.String(length=64), nullable=True),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("is_sensitive_path", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "change_type IN ('create', 'modify', 'delete')",
            name=op.f("ck_code_changes_change_type"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_code_changes_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["agent_steps.id"],
            name=op.f("fk_code_changes_step_id_agent_steps"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.id"],
            name=op.f("fk_code_changes_tool_call_id_tool_calls"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_changes")),
    )
    op.create_index(op.f("ix_code_changes_run_id"), "code_changes", ["run_id"], unique=False)
    op.create_table(
        "test_results",
        sa.Column("test_run_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.String(length=1000), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "passed",
                "failed",
                "error",
                "skipped",
                name="test_outcome",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error', 'skipped')",
            name=op.f("ck_test_results_test_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["test_run_id"],
            ["test_runs.id"],
            name=op.f("fk_test_results_test_run_id_test_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_results")),
    )
    op.create_index(
        op.f("ix_test_results_test_run_id"), "test_results", ["test_run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_test_results_test_run_id"), table_name="test_results")
    op.drop_table("test_results")
    op.drop_index(op.f("ix_code_changes_run_id"), table_name="code_changes")
    op.drop_table("code_changes")
    op.drop_index(op.f("ix_tool_calls_step_id"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_run_id"), table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index(op.f("ix_test_runs_run_id"), table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_table("run_events")
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_pull_requests_repository_id"), table_name="pull_requests")
    op.drop_table("pull_requests")
    op.drop_index(op.f("ix_eval_results_eval_case_id"), table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_index(op.f("ix_approvals_run_id"), table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("agent_steps")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_repository_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_created_at"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_issues_repository_id"), table_name="issues")
    op.drop_table("issues")
    op.drop_table("repositories")
    op.drop_index(op.f("ix_github_credentials_user_id"), table_name="github_credentials")
    op.drop_table("github_credentials")
    op.drop_index(op.f("ix_eval_runs_dataset_id"), table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_index(op.f("ix_users_login"), table_name="users")
    op.drop_table("users")
    op.drop_table("eval_datasets")
