"""AriaError 계층 → HTTP JSON 응답 매핑 (adapter 쪽, HTTP를 아는 유일한 곳).

스프링 @ControllerAdvice에 해당. base AriaError 하나에 핸들러를 걸면
Starlette가 MRO로 모든 서브클래스를 잡는다. 상태 코드는 예외 '타입'으로 결정하고,
응답 스키마는 {"error": {"code", "message"}}로 일관되게 낸다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aria.common.errors import (
    AriaError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnauthorizedError,
    ValidationError,
)

_STATUS: dict[type[AriaError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    UnauthorizedError: 401,
    PermissionDeniedError: 403,
}


def _status_for(exc: AriaError) -> int:
    for exc_type, status in _STATUS.items():
        if isinstance(exc, exc_type):
            return status
    return 500  # bare AriaError / 매핑되지 않은 서브클래스


async def _handle_aria_error(request: Request, exc: AriaError) -> JSONResponse:
    return JSONResponse(
        status_code=_status_for(exc),
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AriaError, _handle_aria_error)  # type: ignore[arg-type]
