"""아웃바운드 포트: 메시지 중복 처리 방지 (claim).

at-least-once로 소비하면 같은 메시지가 두 번 온다. 생성에서 그 대가는 두 가지다 —
**LLM을 두 번 호출하는 비용**과 **같은 응답이 방에 두 번 나가는 것**. 뒤엣것이 특히
나쁘다: 시청자 눈에 그대로 보인다.

**"봤음" 표시가 아니라 claim이다.** 잡고 → 처리하고 → **실패하면 놓는다**. 표시만
남기면 일시 실패가 영구 유실이 되어 at-least-once로 바꾼 의미가 사라진다. 놓지 못한
채 프로세스가 죽는 경우는 TTL이 받는다 — 그래서 TTL은 재전달 창보다 길어야 한다.

**왜 DB가 아니라 Redis인가.** 워커는 DB를 모른다(슬롯·생성·발행이 전부다). 여기에
DB를 들이면 그 설계가 깨진다. 슬롯이 이미 Redis에 의존하므로 새 의존도 아니다.
Redis가 죽으면 중복을 못 막지만, 그때는 슬롯도 이미 안 돈다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class ProcessedRegistry(Protocol):
    async def claim(self, msg_id: UUID) -> bool:
        """이 메시지를 내가 처리하겠다고 선점한다. 이미 누군가 잡았으면 False.

        False는 "중복이니 건너뛰라"는 뜻이다.
        """
        ...

    async def release(self, msg_id: UUID) -> None:
        """claim을 놓는다. **처리에 실패했을 때만** 부른다 — 재전달이 다시 시도할 수
        있게. 성공한 claim은 TTL이 지날 때까지 남아 중복을 막는다.
        """
        ...
