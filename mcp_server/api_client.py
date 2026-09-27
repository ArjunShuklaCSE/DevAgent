"""A small client for the DevAgent REST API, used by the MCP run-control tools."""

from typing import Any

import httpx
from pydantic import SecretStr

SESSION_COOKIE = "devagent_session"


class ApiError(Exception):
    """The DevAgent API refused or failed a request; ``code`` comes from its error envelope."""

    def __init__(self, status: int | None, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class DevAgentApi:
    def __init__(
        self,
        base_url: str,
        *,
        session_cookie: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        cookies = {SESSION_COOKIE: session_cookie.get_secret_value()} if session_cookie else None
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            cookies=cookies,
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401 - JSON returned to the MCP client as-is
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise ApiError(None, "api_unreachable", f"DevAgent API unreachable: {exc}") from exc
        if response.is_success:
            return response.json()
        try:
            error = response.json()["error"]
            code, message = str(error["code"]), str(error["message"])
        except (ValueError, KeyError, TypeError):
            code, message = "api_error", f"HTTP {response.status_code}"
        raise ApiError(response.status_code, code, message)

    async def repositories(self) -> Any:  # noqa: ANN401
        return await self._request("GET", "/repositories", params={"limit": 100})

    async def samples(self) -> Any:  # noqa: ANN401
        return await self._request("GET", "/repositories/samples")

    async def add_repository(self, body: dict[str, Any]) -> Any:  # noqa: ANN401
        return await self._request("POST", "/repositories", json=body)

    async def runs(self, status: str | None, limit: int) -> Any:  # noqa: ANN401
        params: dict[str, str | int] = {"limit": limit}
        if status:
            params["status"] = status
        return await self._request("GET", "/runs", params=params)

    async def run(self, run_id: str) -> Any:  # noqa: ANN401
        return await self._request("GET", f"/runs/{run_id}")

    async def create_run(self, body: dict[str, Any]) -> Any:  # noqa: ANN401
        return await self._request("POST", "/runs", json=body)

    async def cancel(self, run_id: str, reason: str) -> Any:  # noqa: ANN401
        return await self._request("POST", f"/runs/{run_id}/cancel", json={"reason": reason})

    async def diff(self, run_id: str) -> Any:  # noqa: ANN401
        return await self._request("GET", f"/runs/{run_id}/diff")

    async def trace(self, run_id: str) -> Any:  # noqa: ANN401
        return await self._request("GET", f"/runs/{run_id}/trace")
