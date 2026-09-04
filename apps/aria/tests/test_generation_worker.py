"""generation-worker의 Kafka 계층 — `TestKafkaBroker`로 브로커 없이 검증한다.

다른 테스트들은 `DirectEventBus`로 발행을 곧바로 워커에 넘겨 배관 전체를 태운다.
거기서 유일하게 빠지는 조각이 **FastStream의 토픽 라우팅**이고, 여기가 그것을 본다:
어느 토픽으로 나갔는지, 그 토픽 구독자가 실제로 깨어나는지, 키가 실렸는지.

`TestKafkaBroker`는 브로커를 인메모리로 갈아끼우므로 CI가 hermetic하게 남는다 —
실제 브로커를 붙이는 것은 로컬 compose의 몫이다.
"""

from uuid import UUID, uuid4

import pytest
from faststream.kafka import KafkaBroker, TestKafkaBroker

from aria.common.kafka import KafkaEventBus
from aria.contexts.chat.adapter.inbound.worker import router as worker_router
from aria.contexts.chat.application.generation import (
    RESPONSE_REQUESTED,
    SUPERCHAT_REQUESTED,
    GenerationRequest,
    GenerationRequestPublisher,
)
from aria.contexts.chat.domain.source import ChatSource


class _RecordingWorker:
    """`ResponseGenerationService` 자리에 꽂는 스텁 — 무엇이 도착했는지만 본다."""

    def __init__(self) -> None:
        self.handled: list[GenerationRequest] = []

    async def handle(self, request: GenerationRequest) -> None:
        self.handled.append(request)


@pytest.fixture
def worker() -> _RecordingWorker:
    return _RecordingWorker()


@pytest.fixture
def broker(worker: _RecordingWorker) -> KafkaBroker:
    broker = KafkaBroker()
    worker_router.register(broker, worker, group_id="test-generation")
    return broker


async def test_chat_request_reaches_the_worker(
    broker: KafkaBroker, worker: _RecordingWorker
) -> None:
    room, persona = uuid4(), uuid4()

    async with TestKafkaBroker(broker):
        await GenerationRequestPublisher(KafkaEventBus(broker)).request(
            room, persona, ChatSource.CHAT, "안녕하세요"
        )

    [received] = worker.handled
    assert received.room_id == room
    assert received.persona_id == persona
    assert received.source is ChatSource.CHAT
    assert received.prompt == "안녕하세요"


async def test_superchat_request_reaches_the_worker(
    broker: KafkaBroker, worker: _RecordingWorker
) -> None:
    # 토픽이 갈라져 있어도 같은 워커가 받는다 — 우선순위는 코디네이터가 지키고,
    # 토픽 분리는 후원 쪽만 따로 재처리·DLQ를 걸 수 있게 하기 위한 것이다.
    async with TestKafkaBroker(broker):
        await GenerationRequestPublisher(KafkaEventBus(broker)).request(
            uuid4(), uuid4(), ChatSource.SUPERCHAT, "고마워요"
        )

    [received] = worker.handled
    assert received.source is ChatSource.SUPERCHAT


async def test_each_source_goes_to_its_own_topic(broker: KafkaBroker) -> None:
    chat = GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")
    superchat = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.SUPERCHAT, "고마워"
    )
    # idle·story도 일반 토픽이다 — 별도 토픽은 슈퍼챗 하나뿐이다.
    idle = GenerationRequest.create(uuid4(), uuid4(), ChatSource.IDLE, "…")

    assert chat.to_event().stream == RESPONSE_REQUESTED
    assert superchat.to_event().stream == SUPERCHAT_REQUESTED
    assert idle.to_event().stream == RESPONSE_REQUESTED


def test_partition_key_is_the_room() -> None:
    # 같은 방의 요청이 같은 파티션에 들어가야 순서가 보장된다.
    room = uuid4()
    event = GenerationRequest.create(room, uuid4(), ChatSource.CHAT, "안녕").to_event()

    assert event.key == str(room)


def test_payload_round_trips() -> None:
    # 발행 쪽과 소비 쪽이 같은 표현을 읽는지. 여기가 어긋나면 워커가 조용히 죽는다.
    original = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.STORY, "사연입니다"
    )

    restored = GenerationRequest.from_payload(original.to_payload())

    assert restored == original
    assert isinstance(restored.room_id, UUID)
