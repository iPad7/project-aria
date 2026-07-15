"""인증(authN) — 횡단 관심사.

Bearer 토큰을 검증해 호출 주체(Principal)를 만든다. 어느 컨텍스트든 identity를
import하지 않고 "누가 요청했나"를 얻을 수 있는 단일 진입점이다. identity는 로그인 시
토큰을 '발급'하고, 여기서는 그 토큰을 '검증'만 한다(같은 secret).

Principal은 user_id만 담는다 — 전체 User(프로필·상태)는 identity 컨텍스트의 소유물이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aria.common.config import settings
from aria.common.errors import UnauthorizedError

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: UUID


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise UnauthorizedError("인증이 필요합니다", code="not_authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return Principal(user_id=UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError(
            "유효하지 않은 토큰입니다", code="invalid_token"
        ) from exc
