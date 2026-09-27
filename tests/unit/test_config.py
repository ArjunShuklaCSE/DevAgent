import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVAGENT_ENVIRONMENT", "production")
    monkeypatch.setenv("DEVAGENT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("DEVAGENT_DATABASE_URL", "postgresql+asyncpg://u:hunter2@db:5432/x")
    monkeypatch.setenv("DEVAGENT_CORS_ORIGINS", '["https://devagent.example"]')

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert settings.log_level == "WARNING"
    assert settings.database_url.get_secret_value().endswith("@db:5432/x")
    assert settings.cors_origins == ["https://devagent.example"]


def test_connection_strings_are_not_rendered() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://u:hunter2@db/x",  # type: ignore[arg-type]
        _env_file=None,
    )
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in settings.model_dump_json()


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVAGENT_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_are_immutable() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]
