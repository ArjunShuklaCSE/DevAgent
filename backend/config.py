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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (read once from the environment)."""
    return Settings()
