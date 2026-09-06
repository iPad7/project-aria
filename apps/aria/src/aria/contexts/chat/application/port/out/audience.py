"""아웃바운드 포트: 지금 이 방을 보고 있는 사람 수.

**왜 필요한가.** 진행 루프는 `live` 방을 전부 훑고 조용하면 말을 건다. 그래서 아무도
보지 않는 방도 영원히 혼잣말했다 — 데모로 열어 둔 방 16개가 분당 160회씩 LLM을
호출하고 있었다. 비용 문제이기 이전에 제품이 이상하다: 시청자가 0명인데 떠드는
스트리머는 없다.

**`ActivityTracker`와 다른 것을 센다.** 저쪽은 "마지막으로 무슨 일이 있었나"(시간),
여기는 "지금 누가 듣고 있나"(존재). 방금 말한 방이 조용해진 것과, 방금 마지막 시청자가
나간 것은 다른 사건이라 포트도 나눈다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class RoomAudience(Protocol):
    async def viewer_count(self, room_id: UUID) -> int:
        """이 방을 구독 중인 연결 수. 아무도 없으면 0."""
        ...
