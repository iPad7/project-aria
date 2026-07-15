"""아웃바운드 포트: 액세스 토큰 발급.

토큰 '검증'은 공통 authN(common.auth)의 몫이므로 여기엔 '발급'만 둔다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class TokenService(Protocol):
    def issue_access_token(self, user_id: UUID) -> str: ...
