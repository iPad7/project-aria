"""아웃바운드 포트: 액세스 토큰 발급.

토큰 '검증'은 공통 authN(common.auth)의 몫이므로 여기엔 '발급'만 둔다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class TokenService(Protocol):
    def issue_access_token(self, user_id: UUID, *, is_staff: bool = False) -> str:
        """액세스 토큰 발급. 관리자 여부는 클레임으로 실어 보낸다 —
        검증 측(common.auth)이 identity를 import하지 않고 권한을 알 수 있게.
        """
        ...
