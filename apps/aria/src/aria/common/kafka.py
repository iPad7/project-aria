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

import asyncio

from faststream.kafka import KafkaBroker

from aria.common.config import settings
from aria.common.eventbus import Event

broker: KafkaBroker = KafkaBroker(settings.kafka_bootstrap_servers)

# 동시 발행 여럿이 같이 연결을 시도하지 않도록.
_connect_lock = asyncio.Lock()


def get_broker() -> KafkaBroker:
    return broker


async def _ensure_connected(broker: KafkaBroker) -> None:
    """아직 연결되지 않았으면 연결한다.

    상태를 우리가 따로 들고 있지 않고 `broker.running`을 본다. 모듈 전역 플래그로
    두면 브로커가 여럿일 때(테스트의 `TestKafkaBroker`가 그렇다) 엉뚱한 브로커의
    연결 여부를 보게 된다.
    """
    if broker.running:
        return
    async with _connect_lock:
        if not broker.running:
            await broker.connect()


class KafkaEventBus:
    """`EventBusPort`의 유일한 구현.

    `Event.key`를 Kafka 메시지 키로 넘긴다 — 같은 키는 같은 파티션에 들어가므로
    한 방(room_id)의 요청 순서가 보장된다. 이게 없으면 파티션이 여럿일 때 같은 방의
    메시지가 순서를 잃는다.

    **첫 발행 때 연결한다.** 워커(FastStream)는 프레임워크가 생명주기를 잡아 주지만
    api(FastAPI)는 그렇지 않아, 연결 없이 발행하면 `IncorrectState`로 죽는다. 실제로
    그렇게 죽었다 — 테스트는 이 포트를 가짜로 갈아끼우므로 잡히지 않았다.

    startup에서 연결하지 않는 이유: Kafka가 잠깐 늦게 떠도 api는 떠야 하고(채팅
    말고도 하는 일이 많다), 반대로 startup에서 기다리면 브로커 없는 환경에서 기동이
    막힌다. 지연 연결이면 발행하는 순간에만 필요해진다.
    """

    def __init__(self, broker: KafkaBroker) -> None:
        self._broker = broker

    async def publish(self, event: Event) -> None:
        await _ensure_connected(self._broker)
        await self._broker.publish(
            event.payload, topic=event.stream, key=event.key.encode()
        )
