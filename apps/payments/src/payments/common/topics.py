"""토픽 선언 — payments가 **자기가 발행하는** 토픽의 파티션 수를 정한다.

**왜 소비자(aria)만 선언해서는 안 되는가.** 실제로 겪었다: 종단 확인에서
`payments.credit-purchase-confirmed`가 파티션 1개로, `payments.credit-refunded`는
3개로 생겼다. 차이는 **누가 먼저 그 토픽에 손을 댔는가**였다 — relay가 먼저 발행한
토픽은 브로커가 자동 생성해 1개가 됐고, aria의 워커가 먼저 선언한 토픽만 3개였다.

그리고 **발행이 거의 언제나 먼저다.** 데이터를 만드는 쪽이 payments이기 때문이다.
소비자 쪽 선언은 이미 생긴 토픽의 파티션을 바꾸지 않으므로(그건 순서 보장을 끊는
일이라 일부러 안 한다) 영영 1개로 남는다. 파티션 1개면 wallet-worker를 몇 대
띄우든 하나만 일한다.

그래서 **양쪽이 선언한다.** 멱등이라 겹쳐도 무해하고, 누가 먼저 뜨든 3개가 된다.
"""

from __future__ import annotations

import logging

from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from payments.application.topics import CREDIT_PURCHASE_CONFIRMED, CREDIT_REFUNDED
from payments.common.config import settings

logger = logging.getLogger(__name__)

# aria의 `common/topics.py`와 같은 값. 두 서비스가 같은 클러스터의 같은 토픽을 두고
# 다른 수를 주장하면, 먼저 뜬 쪽이 이기는 경주가 된다.
_PARTITIONS = 3
# 단일 노드 개발 브로커라 1. 운영에서는 최소 3이며 그건 IaC의 몫이다.
_REPLICATION = 1

# DLQ는 여기서 만들지 않는다 — payments는 DLQ에 쓰지 않고, 소비 실패를 다루는 것은
# 소비자(aria)의 일이다. 자기가 쓰는 토픽만 선언한다.
PRODUCED = [CREDIT_PURCHASE_CONFIRMED, CREDIT_REFUNDED]


async def ensure_topics(topics: list[str]) -> None:
    """토픽이 없으면 만든다. 이미 있으면 아무 것도 하지 않는다(멱등).

    **기존 토픽의 파티션 수는 바꾸지 않는다.** 늘리면 키→파티션 매핑이 바뀌어 같은
    사용자의 과거·미래 메시지가 다른 파티션에 가고, 순서 보장이 그 지점에서 끊긴다.
    운영자가 알고 하는 편이 낫다.
    """
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [
            NewTopic(name, num_partitions=_PARTITIONS, replication_factor=_REPLICATION)
            for name in topics
            if name not in existing
        ]
        if not missing:
            return
        await admin.create_topics(missing)
        logger.info("토픽 생성: %s", [t.name for t in missing])
    finally:
        await admin.close()
