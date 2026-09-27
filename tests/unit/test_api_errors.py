from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.errors import NotFoundError, register_error_handlers
from backend.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from tests.conftest import StaticProbe, client_for


def _app_with_failing_routes() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Run not found", {"run_id": "abc"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @app.get("/typed/{n}")
    async def typed(n: int) -> dict[str, int]:
        return {"n": n}

    return app


def _client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_app_error_uses_envelope() -> None:
    async with _client(_app_with_failing_routes()) as client:
        response = await client.get("/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Run not found", "details": {"run_id": "abc"}}
    }


async def test_unknown_route_uses_envelope(settings: Settings) -> None:
    async with client_for(settings, [StaticProbe("database")]) as client:
        response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_validation_error_uses_envelope() -> None:
    async with _client(_app_with_failing_routes()) as client:
        response = await client.get("/typed/not-a-number")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["errors"][0]["loc"] == ["path", "n"]


async def test_unexpected_error_hides_internals() -> None:
    async with _client(_app_with_failing_routes()) as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "Internal server error",
        "details": {},
    }
    assert "secret internals" not in response.text


async def test_request_id_is_echoed_or_generated() -> None:
    async with _client(_app_with_failing_routes()) as client:
        echoed = await client.get("/typed/1", headers={REQUEST_ID_HEADER: "abc-123"})
        generated = await client.get("/typed/1")
        rejected = await client.get("/typed/1", headers={REQUEST_ID_HEADER: "bad id\r\n"})
    assert echoed.headers[REQUEST_ID_HEADER] == "abc-123"
    assert len(generated.headers[REQUEST_ID_HEADER]) == 32
    assert rejected.headers[REQUEST_ID_HEADER] != "bad id\r\n"
