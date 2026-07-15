"""TokenService 포트의 JWT 구현 (PyJWT)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from aria.common.errors import UnauthorizedError


class JwtTokenService:
    def __init__(self, secret: str, algorithm: str, ttl_seconds: int) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl_seconds = ttl_seconds

    def issue_access_token(self, user_id: UUID) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(seconds=self._ttl_seconds),
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def read_subject(self, token: str) -> UUID:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            return UUID(payload["sub"])
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise UnauthorizedError(
                "유효하지 않은 토큰입니다", code="invalid_token"
            ) from exc
