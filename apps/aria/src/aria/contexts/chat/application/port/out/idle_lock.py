"""아웃바운드 포트: idle 진행 락 (방별).

**코디네이터가 있는데 왜 또 락인가.** `ResponseCoordinator`만으로도 중복 발화는
막힌다 — 인스턴스 둘이 같은 방에 요청을 넣어도 슬롯은 하나만 잡히니까. 하지만
그때는 이미 늦다: `StoryFeedPort.claim_next_pending`이 **사연을 진짜로 소비해
버린다**(pending → reading). 슬롯을 못 잡은 쪽의 사연은 읽히지도 않고 큐에서
사라진다.

그래서 문을 하나 앞에 둔다. 이 락은 "이 방의 idle 진행을 내가 맡는다"는 뜻이고,
**사연을 claim하기 전에** 잡는다.

TTL이 필요한 이유: 락을 쥔 워커가 죽으면 그 방은 영영 idle 진행을 못 하게 된다.
`ResponseCoordinator`가 같은 이유로 TTL을 두는 것과 같다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class IdleLock(Protocol):
    async def acquire(self, room_id: UUID) -> bool:
        """이 방의 idle 진행을 맡는다. 이미 누군가 맡고 있으면 False."""
        ...

    async def release(self, room_id: UUID) -> None:
        """맡은 것을 놓는다. 한 번의 idle 진행이 끝나면(성공이든 실패든) 부른다."""
        ...
