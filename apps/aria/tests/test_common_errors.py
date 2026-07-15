import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from aria.common.errors import (
    AriaError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnauthorizedError,
    ValidationError,
)
from aria.common.exception_handler import register_exception_handlers


def _client_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (NotFoundError("없음"), 404, "not_found"),
        (ConflictError("충돌"), 409, "conflict"),
        (ValidationError("규칙 위반"), 422, "validation_error"),
        (UnauthorizedError("인증 필요"), 401, "unauthorized"),
        (PermissionDeniedError("권한 없음"), 403, "permission_denied"),
        (AriaError("알 수 없음"), 500, "error"),
    ],
)
def test_error_maps_to_http(exc: AriaError, status: int, code: str) -> None:
    resp = _client_raising(exc).get("/boom")
    assert resp.status_code == status
    assert resp.json() == {"error": {"code": code, "message": exc.message}}


def test_custom_code_overrides_default() -> None:
    err = NotFoundError("페르소나 없음", code="persona_not_found")
    assert err.code == "persona_not_found"
    resp = _client_raising(err).get("/boom")
    assert resp.json()["error"]["code"] == "persona_not_found"
