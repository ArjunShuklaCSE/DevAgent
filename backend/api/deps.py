"""FastAPI dependencies resolved from ``app.state`` (set in the lifespan)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings
from backend.crypto import CryptoError, SecretBox
from backend.errors import UnauthenticatedError
from backend.event_bus import EventBus
from backend.services.auth import AuthService
from backend.services.runs import RunService
from database.models import User


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.event_bus
    return bus


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


def get_samples_root(request: Request) -> Path:
    root: Path = request.app.state.samples_root
    return root


def get_run_service(request: Request) -> RunService:
    return RunService(
        request.app.state.session_factory, request.app.state.event_bus, request.app.state.run_queue
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
SamplesRootDep = Annotated[Path, Depends(get_samples_root)]


# --------------------------------------------------------------------------- auth

SESSION_COOKIE = "devagent_session"
SESSION_PURPOSE = "session"


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


async def get_current_user(
    request: Request, auth: Annotated[AuthService, Depends(get_auth_service)]
) -> User | None:
    """The signed-in user from the session cookie, or None."""
    box: SecretBox | None = request.app.state.secret_box
    cookie = request.cookies.get(SESSION_COOKIE)
    if box is None or not cookie:
        return None
    settings: Settings = request.app.state.settings
    try:
        payload = box.unseal(SESSION_PURPOSE, cookie, settings.session_max_age_seconds)
        user_id = UUID(str(payload["user_id"]))
    except (CryptoError, KeyError, ValueError):
        return None
    return await auth.get_user(user_id)


async def require_user(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> User | None:
    """With GitHub sign-in configured, every run and repository endpoint needs a session.

    Without it (local, single-user setups) the API is open and bound to localhost.
    """
    settings: Settings = request.app.state.settings
    if settings.github_oauth_enabled and user is None:
        raise UnauthenticatedError("Sign in with GitHub to use DevAgent")
    return user


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUserDep = Annotated[User | None, Depends(get_current_user)]
RequiredUserDep = Annotated[User | None, Depends(require_user)]
