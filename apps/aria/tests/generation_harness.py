"""생성 배관을 한 프로세스에서 태우는 테스트 하네스.

C-4-1 이후 응답은 요청이 끝난 뒤 워커가 만들어 방 채널로 발행한다. 테스트에서
브로커와 워커 프로세스를 띄우지 않고도 그 경로 전체를 확인하려면, 발행을 곧바로
워커 핸들러로 넘겨주는 이벤트 버스가 필요하다.

**무엇을 검증하고 무엇을 검증하지 않는지가 분명하다.** 이 하네스는 유스케이스 →
`Event` → 페이로드 → `from_payload` → 워커 서비스 → 코디네이터·LLM·브로드캐스터
전부를 실제 코드로 태운다. 태우지 않는 유일한 조각은 FastStream의 토픽 라우팅이고,
그건 `test_generation_worker.py`가 `TestKafkaBroker`로 따로 본다.
"""

from __future__ import annotations

from redis.asyncio import Redis

from aria.common.eventbus import Event
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.generation import (
    GenerationRequest,
    ResponseGenerationService,
)


class DirectEventBus:
    """`EventBusPort` 구현 — 발행을 그 자리에서 워커에게 넘긴다.

    실제 Kafka는 비동기라 응답이 요청보다 늦게 오지만 여기서는 즉시 돈다. 그래서
    "응답이 나왔는가"는 볼 수 있어도 "얼마나 늦게 오는가"는 볼 수 없다 — 후자는
    이 테스트들의 관심사가 아니다.
    """

    def __init__(self, service: ResponseGenerationService) -> None:
        self._service = service
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)
        await self._service.handle(GenerationRequest.from_payload(event.payload))


class RecordingEventBus:
    """발행만 기록하고 생성은 하지 않는 `EventBusPort` 구현.

    "요청 경로가 무엇을 언제 발행했는가"만 보고 싶을 때 쓴다 — 워커가 개입하면
    프레임 순서에 응답까지 끼어들어 정작 보려던 것이 흐려진다.
    """

    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


def direct_bus(redis: Redis) -> DirectEventBus:
    """워커의 조립을 테스트용 Redis 하나로 재현한다 — `workers/generation.py`와 같은 모양."""
    return DirectEventBus(
        ResponseGenerationService(
            coordinator=RedisResponseCoordinator(redis),
            llm=StubPersonaLLM(),
            broadcaster=RedisRoomBroadcaster(redis),
        )
    )
