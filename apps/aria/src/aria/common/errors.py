"""도메인/애플리케이션 예외 계층.

여기는 HTTP를 모른다 — 상태 코드 매핑은 adapter 쪽(exception_handler)이 한다.
`code`는 클라이언트가 분기에 쓸 수 있는 안정적인 machine-readable 슬러그.
"""

from __future__ import annotations


class AriaError(Exception):
    """모든 도메인/애플리케이션 예외의 뿌리."""

    code = "error"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.__class__.__name__
        if code is not None:
            self.code = code
        super().__init__(self.message)


class NotFoundError(AriaError):
    """요청한 리소스가 존재하지 않음."""

    code = "not_found"


class ConflictError(AriaError):
    """현재 상태와 충돌(중복 생성, 낙관적 락 위반 등)."""

    code = "conflict"


class ValidationError(AriaError):
    """도메인 불변식 위반(Pydantic 스키마 검증과는 별개의 비즈니스 규칙)."""

    code = "validation_error"


class UnauthorizedError(AriaError):
    """인증되지 않음(누구인지 모름)."""

    code = "unauthorized"


class PermissionDeniedError(AriaError):
    """인증됐으나 권한 없음(누구인지는 알지만 허용 안 됨)."""

    code = "permission_denied"
