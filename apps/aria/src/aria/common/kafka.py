"""Kafka 브로커 + `EventBusPort`의 FastStream 구현 (횡단 인프라, redis.py의 형제).

**왜 컨텍스트가 아니라 common인가.** `EventBusPort`가 커널에 있고 여러 컨텍스트가
같은 백본을 쓴다(chat의 생성 요청, 나중에 payments→wallet의 크레딧 지급). 특정
컨텍스트의 adapter에 두면 다른 컨텍스트가 그것을 import해야 하는데 독립성 계약이
그것을 금지한다. `common/redis.py`·`common/db.py`가 클라이언트를 두는 것과 같은 자리다.
여기는 faststream만 import할 뿐 어떤 컨텍스트도 모르므로 커널 순수성은 유지된다.

브로커 객체를 만드는 것만으로는 연결하지 않는다 — 실제 연결은 `broker.connect()`
시점이라, Kafka 없이도 import와 `/health`가 뜬다(Redis 클라이언트와 같은 성질).
"""

from __future__ import annotations

from faststream.kafka import KafkaBroker

from aria.common.config import settings
from aria.common.eventbus import Event

broker: KafkaBroker = KafkaBroker(settings.kafka_bootstrap_servers)


def get_broker() -> KafkaBroker:
    return broker


class KafkaEventBus:
    """`EventBusPort`의 유일한 구현.

    `Event.key`를 Kafka 메시지 키로 넘긴다 — 같은 키는 같은 파티션에 들어가므로
    한 방(room_id)의 요청 순서가 보장된다. 이게 없으면 파티션이 여럿일 때 같은 방의
    메시지가 순서를 잃는다.
    """

    def __init__(self, broker: KafkaBroker) -> None:
        self._broker = broker

    async def publish(self, event: Event) -> None:
        await self._broker.publish(
            event.payload, topic=event.stream, key=event.key.encode()
        )
