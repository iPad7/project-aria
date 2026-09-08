"""wallet-worker의 Kafka 계층 — `TestKafkaBroker`로 브로커 없이 검증한다.

`test_credit_events.py`는 소비자를 직접 불러 적용 규칙을 본다. 거기서 유일하게 빠지는
조각이 **FastStream의 토픽 라우팅**이고, 여기가 그것을 본다: payments가 보낸 토픽 이름의
구독자가 실제로 깨어나는지, 두 토픽이 각자 다른 처리로 가는지.

토픽 문자열이 **두 repo를 잇는 전부**라, 그 문자열을 잘못 적으면 아무 일도 일어나지
않고 아무 로그도 남지 않는다. 그래서 이름을 실제로 태워 본다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from faststream import AckPolicy
from faststream.kafka import KafkaBroker, TestKafkaBroker

from aria.contexts.wallet.adapter.inbound.worker.router import (
    CreditEventConsumer,
    build_router,
    register,
)
from aria.contexts.wallet.application.credit_events import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    CreditEvent,
)


class _RecordingService:
    """`CreditEventService` 자리에 꽂는 스텁 — 어느 경로로 갔는지만 본다."""

    def __init__(self, error: Exception | None = None) -> None:
        self.granted: list[CreditEvent] = []
        self.recovered: list[CreditEvent] = []
        self._error = error

    def purchase_confirmed(self, event: CreditEvent) -> int:
        self.granted.append(event)
        if self._error is not None:
            raise self._error
        return event.credits

    def refunded(self, event: CreditEvent) -> int:
        self.recovered.append(event)
        if self._error is not None:
            raise self._error
        return 0


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


def _payload(user_id: UUID, kind: str = "purchase") -> dict[str, Any]:
    payment_id = uuid4()
    return {
        "v": 1,
        "payment_id": str(payment_id),
        "user_id": str(user_id),
        "credits": 100,
        "idempotency_key": f"payment:{payment_id}:{kind}",
    }


async def test_each_topic_wakes_its_own_handler() -> None:
    service = _RecordingService()
    broker = KafkaBroker()
    register(broker, CreditEventConsumer(service, _RecordingBus()), group_id="test")

    async with TestKafkaBroker(broker):
        await broker.publish(_payload(uuid4()), topic=CREDIT_PURCHASE_CONFIRMED)
        await broker.publish(_payload(uuid4(), "refund"), topic=CREDIT_REFUNDED)

    assert len(service.granted) == 1
    assert len(service.recovered) == 1


async def test_both_topics_commit_after_the_handler() -> None:
    """at-least-once여야 한다 — 크레딧이 조용히 사라지면 잔액이 비는 사고다.

    `ACK_FIRST`(핸들러 전 커밋)로 두면 프로세스가 죽었을 때 그 지급은 재전달 없이
    사라진다. C-4-2에서 채팅 응답에는 그 손실을 감수했지만 돈은 다르다.
    """
    router = build_router(
        CreditEventConsumer(_RecordingService(), _RecordingBus()), group_id="test"
    )

    policies = {sub.topics[0]: sub.ack_policy for sub in router.subscribers}

    assert policies[CREDIT_PURCHASE_CONFIRMED] is AckPolicy.ACK
    assert policies[CREDIT_REFUNDED] is AckPolicy.ACK


async def test_a_failing_handler_sends_the_message_to_the_dlq() -> None:
    """구독자가 예외를 올려보내면 파티션이 멈춘다 — 삼켜서 DLQ로 보낸다."""
    bus = _RecordingBus()
    service = _RecordingService(error=RuntimeError("원장 실패"))
    broker = KafkaBroker()
    register(broker, CreditEventConsumer(service, bus), group_id="test")

    async with TestKafkaBroker(broker):
        await broker.publish(_payload(uuid4()), topic=CREDIT_PURCHASE_CONFIRMED)

    assert len(bus.published) == 1
    assert bus.published[0].stream == CREDIT_PURCHASE_CONFIRMED + ".dlq"


async def test_the_consumer_group_is_shared_across_replicas() -> None:
    """같은 그룹이라 파티션이 인스턴스들에 나뉜다 — 복제본을 늘리는 것이 곧 확장이다."""
    router = build_router(
        CreditEventConsumer(_RecordingService(), _RecordingBus()),
        group_id="wallet-workers",
    )

    assert {sub.group_id for sub in router.subscribers} == {"wallet-workers"}
