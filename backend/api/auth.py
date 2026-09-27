"""GitHub sign-in: OAuth web flow and a sealed session cookie (ADR 0017)."""

from typing import Annotated
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from backend.api.deps import SESSION_COOKIE, SESSION_PURPOSE, AuthServiceDep, CurrentUserDep
from backend.config import Settings
from backend.crypto import CryptoError, SecretBox, new_state
from backend.errors import AppError
from backend.github.client import GitHubError
from backend.github.oauth import GitHubOAuth
from backend.schemas import AuthStatus, UserOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "devagent_oauth_state"
STATE_PURPOSE = "oauth_state"
STATE_MAX_AGE_SECONDS = 600


class OAuthNotConfiguredError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "oauth_not_configured"


def _safe_next(value: str | None) -> str:
    """Only same-site relative paths, so the flow cannot redirect elsewhere."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _cookie_secure(settings: Settings) -> bool:
    return settings.public_web_url.startswith("https://")


def _oauth(request: Request) -> tuple[GitHubOAuth, SecretBox, Settings]:
    oauth: GitHubOAuth | None = request.app.state.oauth
    box: SecretBox | None = request.app.state.secret_box
    if oauth is None or box is None:
        raise OAuthNotConfiguredError(
            "GitHub sign-in is not configured (set DEVAGENT_GITHUB_CLIENT_ID, "
            "DEVAGENT_GITHUB_CLIENT_SECRET and DEVAGENT_SECRET_KEY)"
        )
    return oauth, box, request.app.state.settings


@router.get("/me", response_model=AuthStatus)
async def me(request: Request, user: CurrentUserDep) -> AuthStatus:
    settings: Settings = request.app.state.settings
    return AuthStatus(
        oauth_enabled=settings.github_oauth_enabled,
        github_token_configured=settings.github_token is not None,
        user=UserOut.model_validate(user) if user is not None else None,
    )


@router.get("/github/login")
async def login(
    request: Request, next_path: Annotated[str | None, Query(alias="next")] = None
) -> Response:
    """Redirect to GitHub's consent screen. The state is bound to a sealed cookie."""
    oauth, box, settings = _oauth(request)
    state = new_state()
    response = RedirectResponse(oauth.authorize_url(state), status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        box.seal(STATE_PURPOSE, {"state": state, "next": _safe_next(next_path)}),
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings),
        path="/api/v1/auth",
    )
    return response


@router.get("/github/callback")
async def callback(
    request: Request,
    auth: AuthServiceDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish sign-in, then send the browser back to the dashboard."""
    oauth, box, settings = _oauth(request)
    web = settings.public_web_url.rstrip("/")

    def fail(reason: str) -> Response:
        response = RedirectResponse(f"{web}/?auth_error={quote(reason)}", status.HTTP_302_FOUND)
        response.delete_cookie(STATE_COOKIE, path="/api/v1/auth")
        return response

    if error:
        return fail(error)
    try:
        sealed = box.unseal(
            STATE_PURPOSE, request.cookies.get(STATE_COOKIE, ""), STATE_MAX_AGE_SECONDS
        )
    except CryptoError:
        return fail("state_expired")
    if not code or not state or sealed.get("state") != state:
        return fail("state_mismatch")
    try:
        token = await oauth.exchange(code)
        user = await auth.sign_in(token)
    except GitHubError as exc:
        logger.warning("oauth_failed", code=exc.code, error=exc.message)
        return fail(exc.code)
    response = RedirectResponse(
        f"{web}{_safe_next(str(sealed.get('next')))}", status.HTTP_302_FOUND
    )
    response.delete_cookie(STATE_COOKIE, path="/api/v1/auth")
    response.set_cookie(
        SESSION_COOKIE,
        box.seal(SESSION_PURPOSE, {"user_id": str(user.id)}),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings),
        path="/",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(auth: AuthServiceDep, user: CurrentUserDep) -> Response:
    if user is not None:
        await auth.sign_out(user.id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
