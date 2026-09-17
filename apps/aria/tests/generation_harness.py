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

from uuid import UUID, uuid4

from redis.asyncio import Redis

from aria.common.eventbus import Event
from aria.common.persona_profile import PersonaProfile
from aria.common.tracing import NoOpTracing
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.generation import (
    GenerationRequest,
    ResponseGenerationService,
)
from aria.contexts.chat.domain.message import RoomMessage
from aria.contexts.chat.domain.room import Room


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


class StubProfiles:
    """`PersonaProfilePort` 스텁.

    기본은 **프로필 없음**이다 — 대부분의 테스트는 인격에 관심이 없고, 그때
    시스템 메시지는 공통 프롬프트로 폴백한다. 인격을 보려는 테스트만 프로필을 준다.
    """

    def __init__(self, profile: PersonaProfile | None = None) -> None:
        self._profile = profile
        self.asked: list[UUID] = []

    async def profile_of(self, persona_id: UUID) -> PersonaProfile | None:
        self.asked.append(persona_id)
        return self._profile


class StubRooms:
    """`RoomRepository` 스텁 — 워커가 방송 주제를 읽는 경로만 재현한다.

    기본은 **주제 없음**이다. 대부분의 테스트는 주제에 관심이 없고, 그때 프롬프트에는
    방송 문단이 아예 들어가지 않는다(말투·나침반과 같은 방침).
    """

    def __init__(self, topic: str = "", *, missing: bool = False) -> None:
        self._topic = topic
        self._missing = missing
        self.asked: list[UUID] = []

    async def get_by_id(self, room_id: UUID) -> Room | None:
        self.asked.append(room_id)
        if self._missing:
            return None
        return Room(
            persona_id=uuid4(), host_id=uuid4(), name="테스트 방", topic=self._topic
        )

    async def add(self, room: Room) -> None: ...

    async def save(self, room: Room) -> None: ...

    async def list_by_status(self, status, *, limit: int, offset: int) -> list[Room]:
        return []


class RecordingTranscript:
    """`TranscriptRepository` 인메모리 구현 — 무엇이 남았는지만 본다.

    기록의 실제 SQL은 `test_transcript.py`가 인메모리 SQLite로 따로 본다. 여기서는
    "유스케이스가 남기기는 하는가"만 보면 되므로 DB를 끌어들이지 않는다.
    """

    def __init__(self) -> None:
        self.appended: list[RoomMessage] = []

    async def append(self, message: RoomMessage) -> None:
        self.appended.append(message)

    async def list_recent(
        self, room_id: UUID, *, limit: int = 50, before: UUID | None = None
    ) -> list[RoomMessage]:
        rows = [m for m in self.appended if m.room_id == room_id]
        return list(reversed(rows))[:limit]


def direct_bus(
    redis: Redis,
    transcript: RecordingTranscript | None = None,
    rooms: StubRooms | None = None,
) -> DirectEventBus:
    """워커의 조립을 테스트용 Redis 하나로 재현한다 — `workers/generation.py`와 같은 모양."""
    return DirectEventBus(
        ResponseGenerationService(
            coordinator=RedisResponseCoordinator(redis),
            llm=StubPersonaLLM(),
            broadcaster=RedisRoomBroadcaster(redis),
            profiles=StubProfiles(),
            tracing=NoOpTracing(),
            transcript=transcript or RecordingTranscript(),
            rooms=rooms or StubRooms(),
        )
    )
