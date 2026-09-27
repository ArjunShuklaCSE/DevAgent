"""structlog configuration with JSON output and secret redaction.

Redaction is a structlog processor, so it applies to every log line from every
package (backend, agent, tools, sandbox, llm) once ``configure_logging`` has run.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, Final

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED: Final = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|dsn)",
    re.IGNORECASE,
)

# Value patterns for credentials that may appear inside otherwise harmless strings.
_SENSITIVE_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub classic/OAuth/app tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained PATs
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic keys
    re.compile(r"sk-(proj-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/]+=*"),  # Authorization headers
)
# user:password@ in URLs (DSNs, authenticated clone URLs): keep the user, drop the secret.
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]*):[^@\s]+@")


def redact_text(value: str) -> str:
    """Mask credentials embedded in free text."""
    redacted = _URL_USERINFO.sub(rf"\g<scheme>\g<user>:{REDACTED}@", value)
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: _redact_item(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def _redact_item(key: str, value: object) -> object:
    if _SENSITIVE_KEY.search(key) and value is not None:
        return REDACTED
    return _redact_value(value)


def redact_secrets(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: redact sensitive keys and credential-shaped values."""
    redacted: MutableMapping[str, Any] = {}
    for key, value in event_dict.items():
        redacted[key] = _redact_value(value) if key == "event" else _redact_item(key, value)
    return dict(redacted)


def build_processors(log_format: str) -> list[Processor]:
    """Processor chain; redaction runs last before rendering so nothing bypasses it."""
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
        renderer,
    ]


def configure_logging(level: str, log_format: str) -> None:
    """Configure structlog and route stdlib logging (uvicorn, arq) through it."""
    numeric_level = logging.getLevelNamesMapping()[level]
    processors = build_processors(log_format)
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Stdlib loggers go through the same redaction + renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=processors[:-2],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_secrets,
            processors[-1],
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)
    for name in ("uvicorn", "uvicorn.error", "arq"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = []
        lib_logger.propagate = True
    # RequestContextMiddleware logs every request with its request_id; avoid duplicates.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
