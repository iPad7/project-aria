"""wallet-worker 인바운드 어댑터 (Kafka 구독).

payments의 relay가 보낸 크레딧 사실을 받아 원장에 적용한다. HTTP 라우터가 요청을 받아
서비스를 부르듯 여기는 메시지를 받아 같은 일을 한다 — 그래서 포트가 아니라 어댑터다.

**둘 다 `ACK`(at-least-once)다.** 생성 쪽은 토픽마다 의미론을 갈랐지만(C-4-2), 여기는
갈릴 여지가 없다. 크레딧이 조용히 사라지면 **돈을 낸 사람의 잔액이 비는** 일이고,
그건 "그 말엔 답을 안 했네"와 비교할 수 있는 손실이 아니다.

**멱등은 원장이 책임진다.** 생성 쪽은 Redis `SET NX` claim을 쓰지만 여기는 쓰지 않는다 —
지급의 유일성은 이미 `idempotency_key`의 DB 유일 제약이 보장하고, 그 위에 Redis claim을
겹치면 두 개의 진실이 생긴다. claim이 남고 DB가 롤백된 순간 그 지급은 영영 안 들어간다.

**async 핸들러에서 sync 리포지토리를 부른다.** wallet의 영속 계층은 sync(SQLModel
Session)라 `anyio.to_thread`로 넘긴다 — `WalletSuperchat`과 같은 방식이다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import partial
from typing import Any

import anyio.to_thread
from faststream import AckPolicy
from faststream.kafka import KafkaBroker, KafkaRouter

from aria.common.eventbus import Event, EventBusPort
from aria.contexts.wallet.application.credit_events import (
    CREDIT_PURCHASE_CONFIRMED,
    CREDIT_REFUNDED,
    CreditEvent,
    CreditEventService,
)

logger = logging.getLogger(__name__)

# 생성 워커와 같은 규약: 재시도 0회, 실패 즉시 DLQ. 사람이 보고 판단한다 — 돈이
# 걸린 메시지라 자동 재시도가 조용히 성공하는 것보다 눈에 띄는 편이 낫다.
DLQ_SUFFIX = ".dlq"


class CreditEventConsumer:
    def __init__(self, service: CreditEventService, events: EventBusPort) -> None:
        self._service = service
        self._events = events

    async def consume(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            event = CreditEvent.from_payload(payload)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "크레딧 이벤트를 해석할 수 없다 — DLQ로 보낸다", exc_info=True
            )
            await self._to_dlq(topic, payload, exc)
            return

        apply = (
            self._service.purchase_confirmed
            if topic == CREDIT_PURCHASE_CONFIRMED
            else self._service.refunded
        )
        try:
            await anyio.to_thread.run_sync(partial(apply, event))
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 삼켜 DLQ로 보낸다
            # 잔액 부족(이미 써 버린 크레딧의 환불)이 여기로 온다. 재시도해도 같은
            # 결과라 DLQ가 맞다 — 사람이 환불 취소든 별도 정산이든 정해야 한다.
            logger.exception(
                "크레딧 적용 실패 — DLQ로 보낸다 payment_id=%s", event.payment_id
            )
            await self._to_dlq(topic, payload, exc)

    async def _to_dlq(
        self, topic: str, payload: dict[str, Any], error: Exception
    ) -> None:
        try:
            await self._events.publish(
                Event(
                    stream=topic + DLQ_SUFFIX,
                    # 원본과 같은 키 → 같은 파티션. DLQ에서도 사용자별 순서가 남는다.
                    key=str(payload.get("user_id", "")),
                    payload={
                        "original_topic": topic,
                        "failed_at": datetime.now(UTC).isoformat(),
                        "error": f"{type(error).__name__}: {error}",
                        "original": payload,
                    },
                )
            )
        except Exception:  # noqa: BLE001 - DLQ까지 실패하면 로그가 마지막 수단이다
            logger.exception("DLQ 발행 실패 — 메시지를 잃는다 topic=%s", topic)


def build_router(consumer: CreditEventConsumer, *, group_id: str) -> KafkaRouter:
    router = KafkaRouter()

    @router.subscriber(
        CREDIT_PURCHASE_CONFIRMED, group_id=group_id, ack_policy=AckPolicy.ACK
    )
    async def on_purchase_confirmed(payload: dict[str, Any]) -> None:
        await consumer.consume(CREDIT_PURCHASE_CONFIRMED, payload)

    @router.subscriber(CREDIT_REFUNDED, group_id=group_id, ack_policy=AckPolicy.ACK)
    async def on_refunded(payload: dict[str, Any]) -> None:
        await consumer.consume(CREDIT_REFUNDED, payload)

    return router


def register(
    broker: KafkaBroker, consumer: CreditEventConsumer, *, group_id: str
) -> None:
    broker.include_router(build_router(consumer, group_id=group_id))
