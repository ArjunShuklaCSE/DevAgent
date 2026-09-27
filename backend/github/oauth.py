"""GitHub OAuth web flow (sign-in). See ADR 0017."""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from backend.github.client import GitHubAuthError, GitHubError

# `repo` lets the signed-in user's token open PRs on repositories they can push to,
# including private ones; `read:user` fills in the profile.
DEFAULT_SCOPES = ("read:user", "repo")


@dataclass(frozen=True)
class OAuthToken:
    access_token: SecretStr
    scopes: list[str]


class GitHubOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        *,
        web_url: str = "https://github.com",
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._web_url = web_url.rstrip("/")
        self._scopes = scopes
        self._transport = transport

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": " ".join(self._scopes),
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"{self._web_url}/login/oauth/authorize?{query}"

    async def exchange(self, code: str) -> OAuthToken:
        async with httpx.AsyncClient(transport=self._transport, timeout=30) as http:
            try:
                response = await http.post(
                    f"{self._web_url}/login/oauth/access_token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret.get_secret_value(),
                        "code": code,
                        "redirect_uri": self._redirect_uri,
                    },
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise GitHubError(f"Could not reach GitHub: {type(exc).__name__}") from exc
        if not response.is_success:
            raise GitHubError(f"OAuth token exchange failed (HTTP {response.status_code})")
        body = response.json()
        # GitHub reports OAuth errors with HTTP 200 and an `error` field.
        if "error" in body or "access_token" not in body:
            raise GitHubAuthError(
                f"GitHub refused the sign-in: {body.get('error_description') or body.get('error')}"
            )
        scopes = [s for s in str(body.get("scope", "")).split(",") if s]
        return OAuthToken(access_token=SecretStr(str(body["access_token"])), scopes=scopes)
