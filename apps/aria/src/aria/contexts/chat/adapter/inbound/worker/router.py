"""generation-worker 인바운드 어댑터 (Kafka 구독).

HTTP 라우터가 요청을 받아 애플리케이션 서비스를 부르듯, 여기는 **메시지**를 받아
같은 일을 한다. 그래서 포트가 아니라 어댑터다 — `EventBusPort`에 `subscribe`를 넣지
않은 이유가 이것이다(`aria/common/eventbus.py`).

**두 토픽을 나란히 구독한다.** `docs/events.md`는 원래 "superchat을 먼저 drain"하는
Kafka 우선순위 패턴을 적어 두었지만, 그건 우선순위 장치가 큐 순서뿐일 때의 이야기다.
`ResponseCoordinator`는 **진행 중인 생성까지 선점**할 수 있어 drain 순서보다 강하다 —
큐를 아무리 잘 골라도 이미 돌고 있는 채팅 응답은 멈추지 못하지만 선점은 멈춘다.
그래서 순서 다툼은 코디네이터에 맡기고 여기서는 둘 다 받는다.

토픽이 여전히 둘로 갈라져 있는 이유는 남아 있다: 소비 지연·재처리·DLQ를 후원 쪽만
따로 다룰 수 있어야 하기 때문이다(C-4-2).
"""

from __future__ import annotations

from typing import Any

from faststream.kafka import KafkaBroker, KafkaRouter

from aria.contexts.chat.application.generation import (
    RESPONSE_REQUESTED,
    SUPERCHAT_REQUESTED,
    GenerationRequest,
    ResponseGenerationService,
)


def build_router(service: ResponseGenerationService, *, group_id: str) -> KafkaRouter:
    """구독을 서비스에 묶어 돌려준다.

    브로커에 직접 데코레이터를 붙이지 않고 라우터를 만들어 주는 이유는 합성 루트가
    **무엇을 주입할지 정하게** 하기 위해서다 — 모듈 import 부작용으로 구독이 생기면
    테스트가 진짜 서비스를 갈아끼울 수 없다.
    """
    router = KafkaRouter()

    # 같은 consumer group이라 파티션이 워커 인스턴스들에 나뉜다 — 복제본을 늘리는
    # 것이 곧 수평 확장이다.
    @router.subscriber(RESPONSE_REQUESTED, group_id=group_id)
    async def on_response_requested(payload: dict[str, Any]) -> None:
        await service.handle(GenerationRequest.from_payload(payload))

    @router.subscriber(SUPERCHAT_REQUESTED, group_id=group_id)
    async def on_superchat_requested(payload: dict[str, Any]) -> None:
        await service.handle(GenerationRequest.from_payload(payload))

    return router


def register(
    broker: KafkaBroker, service: ResponseGenerationService, *, group_id: str
) -> None:
    broker.include_router(build_router(service, group_id=group_id))
