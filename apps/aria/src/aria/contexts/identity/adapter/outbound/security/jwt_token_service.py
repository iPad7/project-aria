"""TokenService 포트의 JWT 구현 (PyJWT). 발급 전용 — 검증은 common.auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt


class JwtTokenService:
    def __init__(self, secret: str, algorithm: str, ttl_seconds: int) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl_seconds = ttl_seconds

    def issue_access_token(self, user_id: UUID) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(seconds=self._ttl_seconds),
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)
