"""Users, their encrypted GitHub credentials, and which token a GitHub call uses."""

from collections.abc import Callable
from uuid import UUID

import structlog
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.crypto import CryptoError, SecretBox
from backend.github.client import GitHubClient
from backend.github.oauth import OAuthToken
from database.models import CredentialKind, GithubCredential, User

logger = structlog.get_logger(__name__)

GitHubFactory = Callable[[SecretStr | None], GitHubClient]


def github_factory(api_url: str) -> GitHubFactory:
    def build(token: SecretStr | None) -> GitHubClient:
        return GitHubClient(token, base_url=api_url)

    return build


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        box: SecretBox | None,
        github: GitHubFactory,
        fallback_token: SecretStr | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._box = box
        self._github = github
        self._fallback = fallback_token

    async def sign_in(self, token: OAuthToken) -> User:
        """Create or update the user behind an OAuth token and store the token encrypted."""
        if self._box is None:
            raise CryptoError("DEVAGENT_SECRET_KEY is not set")
        async with self._github(token.access_token) as client:
            profile = await client.get_user()
        async with self._session_factory() as session, session.begin():
            user = await session.scalar(select(User).where(User.github_id == profile.id))
            if user is None:
                user = User(github_id=profile.id, login=profile.login)
                session.add(user)
            user.login = profile.login
            user.name = profile.name
            user.email = profile.email
            user.avatar_url = profile.avatar_url
            await session.flush()
            await session.execute(
                delete(GithubCredential).where(
                    GithubCredential.user_id == user.id,
                    GithubCredential.kind == CredentialKind.OAUTH,
                )
            )
            session.add(
                GithubCredential(
                    user_id=user.id,
                    kind=CredentialKind.OAUTH,
                    encrypted_token=self._box.encrypt_token(token.access_token),
                    scopes=token.scopes,
                )
            )
        logger.info("user_signed_in", user_id=str(user.id), login=user.login)
        return user

    async def sign_out(self, user_id: UUID) -> None:
        """Forget the user's OAuth token (the user row and their history stay)."""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(GithubCredential).where(
                    GithubCredential.user_id == user_id,
                    GithubCredential.kind == CredentialKind.OAUTH,
                )
            )

    async def get_user(self, user_id: UUID) -> User | None:
        async with self._session_factory() as session:
            return await session.get(User, user_id)

    async def token_for(self, user_id: UUID | None) -> SecretStr | None:
        """The user's own token if signed in, else the configured PAT, else None."""
        if user_id is not None and self._box is not None:
            async with self._session_factory() as session:
                credential = await session.scalar(
                    select(GithubCredential)
                    .where(
                        GithubCredential.user_id == user_id,
                        GithubCredential.kind == CredentialKind.OAUTH,
                    )
                    .order_by(GithubCredential.created_at.desc())
                    .limit(1)
                )
            if credential is not None:
                try:
                    return self._box.decrypt_token(credential.encrypted_token)
                except CryptoError:
                    logger.warning("credential_undecryptable", user_id=str(user_id))
        return self._fallback

    def client(self, token: SecretStr | None) -> GitHubClient:
        return self._github(token)
