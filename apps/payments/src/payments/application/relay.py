"""outbox relay — 기록된 사실을 브로커로 내보낸다.

**한 문장으로:** 미발행 로우를 순서대로 읽어 발행하고, 발행된 것만 표시한다.

*발행 → 마킹* 순서가 전부다. 뒤집으면(마킹 → 발행) 그 사이에 죽었을 때 메시지가
**영원히 사라진다** — 이미 "나갔다"고 표시돼 있어 아무도 다시 보내지 않는다. 이 순서면
같은 위험이 중복 발행으로 바뀌고, 중복은 소비자의 멱등키가 흡수한다. outbox가 사는
이유가 정확히 그 교환이다: **유실을 중복으로 바꾼다.**

그래서 이 relay는 at-least-once다. 소비자(aria wallet-workers)의 원장 유일 제약이
두 번째 적용을 막는다.

**한 건씩 표시한다.** 배치를 다 보내고 한 번에 표시하면 중간에 죽었을 때 배치 전체가
재발행된다. 건당 커밋이 왕복은 많지만, 여기서 아끼는 것보다 재발행 폭을 1로 묶는 편이
낫다 — 폴링 주기가 초 단위라 처리량이 병목이 되는 지점이 아니다.
"""

from __future__ import annotations

import logging

from payments.application.port.out.events import EventPublisher
from payments.application.port.out.repository import OutboxRepository

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        outbox: OutboxRepository,
        events: EventPublisher,
        *,
        batch_size: int,
    ) -> None:
        self._outbox = outbox
        self._events = events
        self._batch_size = batch_size

    async def drain_once(self) -> int:
        """미발행분을 한 바퀴 내보낸다. 실제로 발행한 수를 돌려준다.

        **한 건이 실패하면 거기서 멈춘다.** 건너뛰고 다음을 보내면 같은 사용자의 지급과
        회수가 뒤바뀔 수 있다 — 키가 같아 파티션은 같지만 순서는 우리가 넣는 순서다.
        멈추면 다음 폴링이 같은 자리에서 다시 시작한다.
        """
        pending = self._outbox.fetch_unpublished(self._batch_size)
        published = 0
        for message in pending:
            try:
                await self._events.publish(message.topic, message.key, message.payload)
            except Exception:
                logger.exception(
                    "outbox 발행 실패 — 다음 폴링에 재시도한다 id=%s topic=%s",
                    message.id,
                    message.topic,
                )
                break
            # 여기서 죽으면 이 한 건만 재발행된다. 소비자 멱등키가 흡수한다.
            self._outbox.mark_published(message.id)
            published += 1

        if published:
            logger.info("outbox 발행 %d건", published)
        return published
