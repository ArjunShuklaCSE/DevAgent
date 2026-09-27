"""Request-scoped logging context: every log line within a request carries ``request_id``."""

import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
logger = structlog.get_logger(__name__)
MAX_REQUEST_ID_LENGTH = 128


class RequestContextMiddleware:
    """Pure ASGI middleware (works with streaming/SSE responses, unlike BaseHTTPMiddleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode())
        request_id = _sanitize(incoming.decode("latin-1")) if incoming else uuid.uuid4().hex
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self._app(scope, receive, send_with_id)
            finally:
                logger.info(
                    "http_request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )


def _sanitize(value: str) -> str:
    """Accept caller-supplied ids only if short and header-safe; otherwise mint one."""
    if 0 < len(value) <= MAX_REQUEST_ID_LENGTH and all(c.isalnum() or c in "-_." for c in value):
        return value
    return uuid.uuid4().hex
