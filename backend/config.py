"""Typed application settings, loaded from ``DEVAGENT_*`` environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Process configuration shared by the ``api`` and ``worker`` services.

    Connection strings are ``SecretStr`` because they can carry passwords; they are
    never rendered by ``repr`` or logged.
    """

    model_config = SettingsConfigDict(
        env_prefix="DEVAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://devagent:devagent@localhost:5432/devagent"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    sse_keepalive_seconds: float = Field(default=15.0, gt=0, le=120)

    # Workspaces (worker only)
    workspace_root: str = "/tmp/devagent/workspaces"  # noqa: S108 - per-run subdirs, cleaned up
    max_repo_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_repo_files: int = Field(default=20_000, gt=0)
    clone_timeout_seconds: float = Field(default=300, gt=0, le=3600)

    # Sandbox (worker only; see ADR 0012)
    docker_host: str | None = None  # e.g. tcp://docker-proxy:2375; None = DOCKER_HOST/socket
    sandbox_image: str = "devagent-sandbox:dev"
    sandbox_user: str = Field(default="10001:10001", pattern=r"^[1-9][0-9]*:[1-9][0-9]*$")
    sandbox_cpus: float = Field(default=1.0, gt=0, le=16)
    sandbox_memory_mb: int = Field(default=1024, ge=64)
    sandbox_pids: int = Field(default=256, ge=16)
    sandbox_tmpfs_mb: int = Field(default=256, ge=16)
    sandbox_workspace_volume: str | None = None
    sandbox_install_network: str = "bridge"
    sandbox_install_proxy: str | None = None  # HTTP(S) proxy for dependency installs only
    sandbox_extra_ca_file: str | None = None  # CA bundle for installs behind that proxy
    sandbox_runtime: str | None = None  # e.g. "runsc" (gVisor)
    sandbox_reap_after_seconds: float = Field(default=2 * 3600, gt=0)
    command_policy_path: str = "config/command_policy.yaml"

    # LLM (worker only; see ADR 0014). Model names and prices are configuration.
    llm_model: str | None = None  # must have an entry in the pricing file
    llm_pricing_path: str = "config/model_pricing.yaml"
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=4096, ge=256, le=64_000)
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com"
    prompts_path: str = "agent/prompts"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (read once from the environment)."""
    return Settings()
