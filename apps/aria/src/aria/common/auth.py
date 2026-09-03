"""인증(authN) — 횡단 관심사.

Bearer 토큰을 검증해 호출 주체(Principal)를 만든다. 어느 컨텍스트든 identity를
import하지 않고 "누가 요청했나"를 얻을 수 있는 단일 진입점이다. identity는 로그인 시
토큰을 '발급'하고, 여기서는 그 토큰을 '검증'만 한다(같은 secret).

토큰 검증 자체는 `principal_from_token`으로 분리해 HTTP(Authorization 헤더)와
WebSocket(첫 프레임 auth 메시지)이 같은 로직을 공유한다 — 검증은 전송 방식과 무관.

Principal은 user_id와 관리자 여부만 담는다 — 전체 User(프로필·상태)는 identity
컨텍스트의 소유물이다.

**관리자 여부를 왜 토큰 클레임으로 두나.** DB에서 매번 조회하려면 여기서 identity를
import해야 하는데, 커널은 컨텍스트를 import할 수 없다(`common-kernel-purity`). 토큰
발급은 identity가, 검증은 여기가 하는 기존 분업 위에 클레임 하나를 얹는 것이 유일하게
계약을 지키는 길이다. 대가는 **권한 회수가 재로그인(토큰 만료) 시점에 반영**된다는 것
이며, 관리자 승격/강등 빈도를 생각하면 받아들일 만하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aria.common.config import settings
from aria.common.errors import PermissionDeniedError, UnauthorizedError

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    is_staff: bool = False


def principal_from_token(token: str) -> Principal:
    """JWT를 검증해 Principal을 만든다 — HTTP·WebSocket 공통 검증 지점.

    실패(서명·만료·잘못된 sub)는 UnauthorizedError("invalid_token")로 통일한다.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        # 클레임이 없으면 일반 사용자 — 권한은 명시적으로 있을 때만 준다.
        return Principal(
            user_id=UUID(payload["sub"]),
            is_staff=bool(payload.get("staff", False)),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError(
            "유효하지 않은 토큰입니다", code="invalid_token"
        ) from exc


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise UnauthorizedError("인증이 필요합니다", code="not_authenticated")
    return principal_from_token(credentials.credentials)


def require_staff(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    """관리자 전용 엔드포인트용 의존성. 인증은 됐지만 권한이 없으면 403."""
    if not principal.is_staff:
        raise PermissionDeniedError("관리자 권한이 필요합니다", code="staff_required")
    return principal
