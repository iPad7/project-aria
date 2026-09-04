"""토픽 선언 — 파티션 수를 코드가 정한다.

**자동 생성에 맡기면 파티션이 1개다.** 그러면 워커를 몇 개 띄우든 하나만 일한다 —
consumer group은 파티션 단위로 나눠 갖기 때문이다. 수평 확장이 가능한지가 브로커
기본값에 달려 있는 상태는 좋지 않으므로, 필요한 토픽과 파티션 수를 여기서 선언한다.

**파티션 3인 이유.** 파티션 수는 그 토픽의 **최대 동시 소비자 수**다. 늘리는 건
언제든 되지만 줄이는 건 안 되고, 늘리면 키→파티션 매핑이 바뀌어 같은 방의 과거·미래
메시지가 다른 파티션에 갈 수 있다(순서 보장이 그 경계에서 끊긴다). 그래서 처음부터
"당장 필요한 수"보다 조금 크게 잡는 것이 관용구다. 3은 단일 노드 개발 브로커에서
부담이 없으면서 워커 3대까지 받아 준다.

DLQ도 함께 만든다 — 실패한 뒤에야 토픽이 생기면 그 순간 다시 실패할 여지가 있다.
"""

from __future__ import annotations

import logging

from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from aria.common.config import settings

logger = logging.getLogger(__name__)

_PARTITIONS = 3
# 단일 노드 개발 브로커라 1. 운영에서는 최소 3이어야 하며, 그건 IaC의 몫이다.
_REPLICATION = 1


async def ensure_topics(topics: list[str]) -> None:
    """토픽이 없으면 만든다. 이미 있으면 아무 것도 하지 않는다(멱등).

    **기존 토픽의 파티션 수를 바꾸지는 않는다.** 이미 1 파티션으로 자동 생성된 토픽이
    있다면 여기서 조용히 늘리는 것보다, 운영자가 알고 늘리는 편이 낫다 — 파티션을
    늘리면 키→파티션 매핑이 바뀌어 순서 보장이 그 지점에서 끊기기 때문이다.
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
