import json

import pytest
import structlog

from backend.logging_setup import REDACTED, build_processors, redact_secrets, redact_text

pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "gho_" + "B" * 36,
        "github_pat_" + "11ABCDEFG0" * 5,
        "sk-ant-api03-" + "x" * 40,
        "sk-proj-" + "y" * 40,
    ],
)
def test_credential_shaped_values_are_redacted(secret: str) -> None:
    assert secret not in redact_text(f"using token {secret} for clone")


def test_bearer_header_is_redacted() -> None:
    assert "abc.def.ghi" not in redact_text("Authorization: Bearer abc.def.ghi")


def test_url_password_is_redacted_but_user_kept() -> None:
    out = redact_text("postgresql+asyncpg://devagent:hunter2@db:5432/devagent")
    assert "hunter2" not in out
    assert out == f"postgresql+asyncpg://devagent:{REDACTED}@db:5432/devagent"


def test_authenticated_clone_url_is_redacted() -> None:
    out = redact_text("git clone https://x-access-token:ghs_abcdefghijklmnopqrstuv@github.com/o/r")
    assert "ghs_abcdefghijklmnopqrstuv" not in out


def test_sensitive_keys_are_redacted_recursively() -> None:
    event = {
        "event": "call",
        "github_token": "anything",
        "headers": {"Authorization": "Basic Zm9vOmJhcg==", "Accept": "json"},
        "items": [{"api_key": "k"}, "plain"],
    }
    out = redact_secrets(None, "info", event)
    assert out["github_token"] == REDACTED
    assert out["headers"] == {"Authorization": REDACTED, "Accept": "json"}
    assert out["items"] == [{"api_key": REDACTED}, "plain"]
    assert out["event"] == "call"


def test_rendered_json_log_line_contains_no_secret() -> None:
    processors = build_processors("json")
    token = "ghp_" + "z" * 36
    event_dict: structlog.typing.EventDict = {
        "event": f"cloning with {token}",
        "token": token,
        "detail": {"dsn": "redis://:pw@redis:6379"},
    }
    rendered: object = event_dict
    for processor in processors:
        rendered = processor(None, "info", rendered)  # type: ignore[arg-type]
    assert isinstance(rendered, str)
    assert token not in rendered
    assert "pw@" not in rendered
    assert json.loads(rendered)["token"] == REDACTED
