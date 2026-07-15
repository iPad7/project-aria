"""아웃바운드 포트: 액세스 토큰 발급/검증."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class TokenService(Protocol):
    def issue_access_token(self, user_id: UUID) -> str: ...

    def read_subject(self, token: str) -> UUID:
        """토큰에서 user_id를 꺼낸다. 유효하지 않으면 UnauthorizedError."""
        ...
