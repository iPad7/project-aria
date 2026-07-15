"""identity 조립(합성) — 포트 구현체를 유스케이스에 주입하는 FastAPI 의존성.

해셔·토큰 서비스는 무상태라 모듈 싱글턴, 리포지토리는 요청 세션에 묶인다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from aria.common.config import settings
from aria.common.db import get_session
from aria.common.errors import UnauthorizedError
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
_bearer = HTTPBearer(auto_error=False)


def get_identity_service(
    session: Annotated[Session, Depends(get_session)],
) -> IdentityService:
    return IdentityService(SqlModelUserRepository(session), _hasher, _tokens)


def get_current_user(
    service: Annotated[IdentityService, Depends(get_identity_service)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("인증이 필요합니다", code="not_authenticated")
    user_id = _tokens.read_subject(credentials.credentials)
    return service.get_user(user_id)
