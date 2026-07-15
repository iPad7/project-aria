"""identity 조립(합성) — 포트 구현체를 유스케이스에 주입하는 FastAPI 의존성.

해셔·토큰 서비스는 무상태라 모듈 싱글턴, 리포지토리는 요청 세션에 묶인다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.auth import Principal, get_current_principal
from aria.common.config import settings
from aria.common.db import get_session
from aria.contexts.identity.adapter.outbound.persistence.repository import (
    SqlModelUserRepository,
)
from aria.contexts.identity.adapter.outbound.security.argon2_hasher import (
    Argon2PasswordHasher,
)
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)
from aria.contexts.identity.application.service import IdentityService
from aria.contexts.identity.domain.model import User

_hasher = Argon2PasswordHasher()
_tokens = JwtTokenService(
    secret=settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
    ttl_seconds=settings.jwt_ttl_seconds,
)


def get_identity_service(
    session: Annotated[Session, Depends(get_session)],
) -> IdentityService:
    return IdentityService(SqlModelUserRepository(session), _hasher, _tokens)


def get_current_user(
    service: Annotated[IdentityService, Depends(get_identity_service)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> User:
    # 토큰 검증(누구인가)은 공통 authN이 하고, identity는 그 주체를 User로 해석한다.
    return service.get_user(principal.user_id)
