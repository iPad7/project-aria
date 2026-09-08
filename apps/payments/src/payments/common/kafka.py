"""Kafka 브로커 (횡단 인프라, db.py의 형제).

브로커 객체를 만드는 것만으로는 연결하지 않는다 — 실제 연결은 `connect()` 시점이라
Kafka 없이도 import와 `/health`가 뜬다.

포트 구현(`EventPublisher`)은 여기가 아니라 `adapter/outbound/events.py`에 있다.
aria는 커널 순수성 때문에 둘을 common에 함께 뒀지만, payments에는 그 제약이 없어
어댑터는 어댑터 자리에 둔다.
"""

from __future__ import annotations

from faststream.kafka import KafkaBroker

from payments.common.config import settings

broker: KafkaBroker = KafkaBroker(settings.kafka_bootstrap_servers)


def get_broker() -> KafkaBroker:
    return broker
