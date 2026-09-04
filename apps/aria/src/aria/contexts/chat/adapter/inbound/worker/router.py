"""generation-worker 인바운드 어댑터 (Kafka 구독).

HTTP 라우터가 요청을 받아 애플리케이션 서비스를 부르듯, 여기는 **메시지**를 받아
같은 일을 한다. 그래서 포트가 아니라 어댑터다 — `EventBusPort`에 `subscribe`를 넣지
않은 이유가 이것이다(`aria/common/eventbus.py`).

**두 토픽을 나란히 구독한다.** `docs/events.md`는 원래 "superchat을 먼저 drain"하는
Kafka 우선순위 패턴을 적어 두었지만, 그건 우선순위 장치가 큐 순서뿐일 때의 이야기다.
`ResponseCoordinator`는 **진행 중인 생성까지 선점**할 수 있어 drain 순서보다 강하다 —
큐를 아무리 잘 골라도 이미 돌고 있는 채팅 응답은 멈추지 못하지만 선점은 멈춘다.
그래서 순서 다툼은 코디네이터에 맡기고 여기서는 둘 다 받는다.

**전달 의미론은 토픽마다 다르다**(C-4-2). 그것이 토픽을 갈라 둔 값이다 — 아래
`_ACK_POLICY` 참조. 배달 보증·멱등·DLQ는 전부 이 어댑터의 일이고,
`ResponseGenerationService`는 그런 게 있는 줄 모른다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from faststream import AckPolicy
from faststream.kafka import KafkaBroker, KafkaRouter

from aria.common.eventbus import Event, EventBusPort
from aria.contexts.chat.application.generation import (
    RESPONSE_REQUESTED,
    SUPERCHAT_REQUESTED,
    GenerationRequest,
    ResponseGenerationService,
)
from aria.contexts.chat.application.port.out.dedup import ProcessedRegistry

logger = logging.getLogger(__name__)

# 실패한 메시지가 가는 곳. 재시도는 **하지 않는다** — 인프로세스 재시도는 파티션을
# 막아(head-of-line blocking) 같은 방의 뒤 메시지가 앞 메시지의 백오프를 기다리게
# 하고, 애초에 30초 뒤에 성공한 응답은 이미 늦었다. at-least-once의 값은 "늦게라도
# 성공"이 아니라 **"조용히 사라지지 않는다"**에 있다. DLQ는 사람이 보고 판단한다.
DLQ_SUFFIX = ".dlq"

# 토픽별 전달 의미론. 여기가 C-4-2의 핵심 결정이다.
#
# - 일반 채팅(ACK_FIRST): 핸들러 전에 offset을 커밋한다 → **at-most-once**. 워커가
#   생성 도중 죽으면 그 응답은 없던 일이 된다. 감수하는 이유는 채팅 응답이 시간이
#   지나면 가치를 잃기 때문이다 — "AI가 그 말엔 답을 안 했네" 수준의 손실이다.
# - 후원(ACK): 핸들러가 끝난 뒤 커밋한다 → **at-least-once**. 크래시하면 재전달된다.
#   돈을 낸 사람이 아무 반응도 못 받는 것은 다른 무게의 사고다.
#
# 예외는 두 정책 모두에서 핸들러가 삼켜 DLQ로 보내므로, 둘의 실질적 차이는
# **프로세스가 통째로 죽는 경우**다. 그게 정확히 우리가 구분하려던 경우다.
_ACK_POLICY = {
    RESPONSE_REQUESTED: AckPolicy.ACK_FIRST,
    SUPERCHAT_REQUESTED: AckPolicy.ACK,
}


class GenerationConsumer:
    """배달 보증을 입은 소비 경로.

    claim → 처리 → (실패하면) DLQ + claim 해제. 이 순서가 전부다.

    **claim을 두 토픽 모두에 건다.** at-most-once인 채팅 토픽에는 재전달이 없어 보이지만,
    `ACK_FIRST`의 커밋도 비동기라 리밸런스 시점에 따라 커밋되지 않은 메시지가 다시 올 수
    있다. 중복 응답은 시청자 눈에 그대로 보이므로 양쪽 다 막는다.
    """

    def __init__(
        self,
        service: ResponseGenerationService,
        registry: ProcessedRegistry,
        events: EventBusPort,
    ) -> None:
        self._service = service
        self._registry = registry
        self._events = events

    async def consume(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            request = GenerationRequest.from_payload(payload)
        except (ValueError, KeyError, TypeError) as exc:
            # 읽을 수조차 없는 메시지 — 모르는 스키마 버전이거나 깨진 페이로드다.
            # claim도 걸지 못하므로 그대로 DLQ로 보낸다.
            logger.warning("생성 요청을 해석할 수 없다 — DLQ로 보낸다", exc_info=True)
            await self._to_dlq(topic, payload, exc)
            return

        if not await self._registry.claim(request.msg_id):
            # 이미 누군가 처리했거나 처리 중이다. 두 번째 응답을 내보내지 않는다.
            logger.info("중복 생성 요청 — 건너뛴다 msg_id=%s", request.msg_id)
            return

        try:
            await self._service.handle(request)
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 삼켜 DLQ로 보낸다
            # claim을 놓아 재전달이 다시 시도할 수 있게 한다. 놓지 않으면 일시 실패가
            # 영구 유실이 되어 at-least-once로 바꾼 의미가 사라진다.
            await self._registry.release(request.msg_id)
            logger.exception("생성 실패 — DLQ로 보낸다 msg_id=%s", request.msg_id)
            await self._to_dlq(topic, payload, exc)

    async def _to_dlq(
        self, topic: str, payload: dict[str, Any], error: Exception
    ) -> None:
        """원본과 실패 정황을 함께 남긴다 — 사람이 보고 판단할 수 있어야 하므로.

        DLQ 발행 자체가 실패하면 더 할 수 있는 게 없다. 그 예외까지 올려보내면
        메시지 하나 때문에 소비가 멈추므로, 로그에 남기고 넘어간다.
        """
        try:
            await self._events.publish(
                Event(
                    stream=topic + DLQ_SUFFIX,
                    # 원본과 같은 키 → 같은 파티션. DLQ에서도 방별 순서가 남는다.
                    key=str(payload.get("room_id", "")),
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


def build_router(consumer: GenerationConsumer, *, group_id: str) -> KafkaRouter:
    """구독을 소비자에 묶어 돌려준다.

    브로커에 직접 데코레이터를 붙이지 않고 라우터를 만들어 주는 이유는 합성 루트가
    **무엇을 주입할지 정하게** 하기 위해서다 — 모듈 import 부작용으로 구독이 생기면
    테스트가 진짜 서비스를 갈아끼울 수 없다.
    """
    router = KafkaRouter()

    # 같은 consumer group이라 파티션이 워커 인스턴스들에 나뉜다 — 복제본을 늘리는
    # 것이 곧 수평 확장이다.
    @router.subscriber(
        RESPONSE_REQUESTED,
        group_id=group_id,
        ack_policy=_ACK_POLICY[RESPONSE_REQUESTED],
    )
    async def on_response_requested(payload: dict[str, Any]) -> None:
        await consumer.consume(RESPONSE_REQUESTED, payload)

    @router.subscriber(
        SUPERCHAT_REQUESTED,
        group_id=group_id,
        ack_policy=_ACK_POLICY[SUPERCHAT_REQUESTED],
    )
    async def on_superchat_requested(payload: dict[str, Any]) -> None:
        await consumer.consume(SUPERCHAT_REQUESTED, payload)

    return router


def register(
    broker: KafkaBroker, consumer: GenerationConsumer, *, group_id: str
) -> None:
    broker.include_router(build_router(consumer, group_id=group_id))
