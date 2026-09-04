"""generation-worker의 Kafka 계층 — `TestKafkaBroker`로 브로커 없이 검증한다.

다른 테스트들은 `DirectEventBus`로 발행을 곧바로 워커에 넘겨 배관 전체를 태운다.
거기서 유일하게 빠지는 조각이 **FastStream의 토픽 라우팅**이고, 여기가 그것을 본다:
어느 토픽으로 나갔는지, 그 토픽 구독자가 실제로 깨어나는지, 키가 실렸는지.

`TestKafkaBroker`는 브로커를 인메모리로 갈아끼우므로 CI가 hermetic하게 남는다 —
실제 브로커를 붙이는 것은 로컬 compose의 몫이다.
"""

from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from faststream import AckPolicy
from faststream.kafka import KafkaBroker, TestKafkaBroker
from generation_harness import RecordingEventBus

from aria.common.eventbus import Event
from aria.common.kafka import KafkaEventBus
from aria.contexts.chat.adapter.inbound.worker import router as worker_router
from aria.contexts.chat.adapter.inbound.worker.router import GenerationConsumer
from aria.contexts.chat.application.generation import (
    RESPONSE_REQUESTED,
    SCHEMA_VERSION,
    SUPERCHAT_REQUESTED,
    GenerationRequest,
    GenerationRequestPublisher,
)
from aria.contexts.chat.domain.source import ChatSource


class _RecordingWorker:
    """`ResponseGenerationService` 자리에 꽂는 스텁 — 무엇이 도착했는지만 본다."""

    def __init__(self, error: Exception | None = None) -> None:
        self.handled: list[GenerationRequest] = []
        self._error = error

    async def handle(self, request: GenerationRequest) -> None:
        self.handled.append(request)
        if self._error is not None:
            raise self._error


class _MemoryRegistry:
    """`ProcessedRegistry`의 인메모리 구현 — claim 의미만 재현한다."""

    def __init__(self) -> None:
        self.claimed: set[UUID] = set()

    async def claim(self, msg_id: UUID) -> bool:
        if msg_id in self.claimed:
            return False
        self.claimed.add(msg_id)
        return True

    async def release(self, msg_id: UUID) -> None:
        self.claimed.discard(msg_id)


@pytest.fixture
def worker() -> _RecordingWorker:
    return _RecordingWorker()


@pytest.fixture
def broker(worker: _RecordingWorker) -> KafkaBroker:
    broker = KafkaBroker()
    consumer = GenerationConsumer(worker, _MemoryRegistry(), RecordingEventBus())
    worker_router.register(broker, consumer, group_id="test-generation")
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


# --- 전달 의미론: 토픽마다 다르다 (C-4-2) -----------------------------------


def test_ack_policy_differs_per_topic(broker: KafkaBroker) -> None:
    """토픽을 갈라 둔 값이 여기서 나온다.

    채팅은 핸들러 **전에** 커밋(at-most-once) — 워커가 죽으면 그 응답은 없던 일이
    된다. 후원은 핸들러 **후에** 커밋(at-least-once) — 크래시하면 재전달된다.
    돈을 낸 사람이 아무 반응도 못 받는 것은 다른 무게의 사고다.
    """
    policies = {sub.topics[0]: sub.ack_policy for sub in broker.subscribers}

    assert policies[RESPONSE_REQUESTED] is AckPolicy.ACK_FIRST
    assert policies[SUPERCHAT_REQUESTED] is AckPolicy.ACK


# --- 멱등: 중복 소비를 흡수하는가 -------------------------------------------


@pytest.fixture
def registry() -> _MemoryRegistry:
    return _MemoryRegistry()


@pytest.fixture
def dlq() -> RecordingEventBus:
    return RecordingEventBus()


def _consumer(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> GenerationConsumer:
    return GenerationConsumer(worker, registry, dlq)


async def test_duplicate_delivery_generates_only_once(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    # at-least-once면 같은 메시지가 두 번 온다. 두 번 생성하면 LLM 비용이 두 배고,
    # 무엇보다 같은 응답이 방에 두 번 나가 시청자 눈에 그대로 보인다.
    consumer = _consumer(worker, registry, dlq)
    payload = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.SUPERCHAT, "고마워요"
    ).to_payload()

    await consumer.consume(SUPERCHAT_REQUESTED, payload)
    await consumer.consume(SUPERCHAT_REQUESTED, payload)

    assert len(worker.handled) == 1
    assert dlq.published == []


async def test_different_messages_are_not_confused(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    consumer = _consumer(worker, registry, dlq)
    room = uuid4()

    for _ in range(3):
        await consumer.consume(
            RESPONSE_REQUESTED,
            GenerationRequest.create(
                room, uuid4(), ChatSource.CHAT, "안녕"
            ).to_payload(),
        )

    assert len(worker.handled) == 3


# --- 실패: DLQ로 가고 claim을 놓는가 ----------------------------------------


async def test_failure_goes_to_the_dlq(
    registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    worker = _RecordingWorker(error=RuntimeError("LLM이 죽었다"))
    request = GenerationRequest.create(uuid4(), uuid4(), ChatSource.CHAT, "안녕")

    await _consumer(worker, registry, dlq).consume(
        RESPONSE_REQUESTED, request.to_payload()
    )

    [event] = dlq.published
    assert event.stream == RESPONSE_REQUESTED + ".dlq"
    # 원본과 실패 정황이 함께 남아야 사람이 보고 판단할 수 있다.
    assert event.payload["original_topic"] == RESPONSE_REQUESTED
    assert "LLM이 죽었다" in event.payload["error"]
    assert event.payload["original"]["msg_id"] == str(request.msg_id)
    # 원본과 같은 키 → DLQ에서도 방별 순서가 남는다.
    assert event.key == str(request.room_id)


async def test_failure_releases_the_claim_so_redelivery_can_retry(
    registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    # claim을 놓지 않으면 일시 실패가 영구 유실이 된다 — at-least-once로 바꾼 의미가
    # 사라진다.
    failing = _RecordingWorker(error=RuntimeError("일시적 실패"))
    payload = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.SUPERCHAT, "고마워요"
    ).to_payload()
    await _consumer(failing, registry, dlq).consume(SUPERCHAT_REQUESTED, payload)

    assert registry.claimed == set()

    # 재전달이 오면 이번엔 성공한다.
    healthy = _RecordingWorker()
    await _consumer(healthy, registry, dlq).consume(SUPERCHAT_REQUESTED, payload)

    assert len(healthy.handled) == 1


async def test_failed_generation_is_not_retried_in_process(
    registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    # 재시도 0회가 결정이다 — 인프로세스 재시도는 파티션을 막고, 30초 뒤에 성공한
    # 응답은 이미 늦었다.
    worker = _RecordingWorker(error=RuntimeError("boom"))

    await _consumer(worker, registry, dlq).consume(
        RESPONSE_REQUESTED,
        GenerationRequest.create(
            uuid4(), uuid4(), ChatSource.CHAT, "안녕"
        ).to_payload(),
    )

    assert len(worker.handled) == 1  # 한 번만 시도했다
    assert len(dlq.published) == 1


async def test_dlq_failure_does_not_stop_consumption(registry: _MemoryRegistry) -> None:
    # DLQ 발행까지 실패하면 더 할 수 있는 게 없다. 예외를 올려보내면 메시지 하나
    # 때문에 소비가 멈추므로 로그만 남기고 넘어간다.
    class _BrokenBus:
        async def publish(self, event: object) -> None:
            raise ConnectionError("브로커 다운")

    worker = _RecordingWorker(error=RuntimeError("boom"))
    consumer = GenerationConsumer(worker, registry, _BrokenBus())

    await consumer.consume(
        RESPONSE_REQUESTED,
        GenerationRequest.create(
            uuid4(), uuid4(), ChatSource.CHAT, "안녕"
        ).to_payload(),
    )  # 예외가 새어 나오지 않으면 통과


# --- 스키마 버저닝 ----------------------------------------------------------


async def test_unknown_schema_version_goes_to_the_dlq(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    # 모르는 버전을 반쯤 읽어 이상한 응답을 내보내는 것이 최악이다.
    payload = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.CHAT, "안녕"
    ).to_payload()
    payload["v"] = SCHEMA_VERSION + 1

    await _consumer(worker, registry, dlq).consume(RESPONSE_REQUESTED, payload)

    assert worker.handled == []
    assert len(dlq.published) == 1
    assert "모르는 페이로드 버전" in dlq.published[0].payload["error"]


async def test_payload_without_version_is_read_as_v1(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    # C-4-2 이전에 발행돼 큐에 남아 있던 메시지를 버리지 않는다.
    payload = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.CHAT, "안녕"
    ).to_payload()
    del payload["v"]

    await _consumer(worker, registry, dlq).consume(RESPONSE_REQUESTED, payload)

    assert len(worker.handled) == 1
    assert dlq.published == []


async def test_corrupt_payload_goes_to_the_dlq(
    worker: _RecordingWorker, registry: _MemoryRegistry, dlq: RecordingEventBus
) -> None:
    await _consumer(worker, registry, dlq).consume(
        RESPONSE_REQUESTED, {"v": 1, "msg_id": "not-a-uuid"}
    )

    assert worker.handled == []
    assert len(dlq.published) == 1


def test_payload_carries_its_schema_version() -> None:
    payload = GenerationRequest.create(
        uuid4(), uuid4(), ChatSource.CHAT, "안녕"
    ).to_payload()

    assert payload["v"] == SCHEMA_VERSION


# --- Redis claim 어댑터 ------------------------------------------------------


async def test_redis_claim_is_exclusive() -> None:
    from aria.contexts.chat.adapter.outbound.redis.dedup import RedisProcessedRegistry

    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    registry = RedisProcessedRegistry(redis, ttl_seconds=60)
    msg_id = uuid4()

    assert await registry.claim(msg_id) is True
    assert await registry.claim(msg_id) is False  # 두 번째는 못 잡는다

    await registry.release(msg_id)
    assert await registry.claim(msg_id) is True  # 놓으면 다시 잡힌다


async def test_redis_claims_do_not_collide_across_messages() -> None:
    from aria.contexts.chat.adapter.outbound.redis.dedup import RedisProcessedRegistry

    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    registry = RedisProcessedRegistry(redis, ttl_seconds=60)

    assert await registry.claim(uuid4()) is True
    assert await registry.claim(uuid4()) is True


# --- 발행 전 연결 (실제로 띄워 보고 찾은 버그) ------------------------------


class _FakeBroker:
    """`running`과 `connect()`만 흉내 내는 브로커."""

    def __init__(self, *, running: bool = False) -> None:
        self.running = running
        self.connects = 0
        self.published: list[dict] = []

    async def connect(self) -> None:
        self.connects += 1
        self.running = True

    async def publish(self, payload, *, topic, key) -> None:
        self.published.append({"payload": payload, "topic": topic, "key": key})


async def test_publish_connects_the_broker_first() -> None:
    """워커(FastStream)는 프레임워크가 생명주기를 잡아 주지만 api(FastAPI)는 아니다.

    연결 없이 발행하면 `IncorrectState`로 죽는다 — 실제로 그렇게 죽었고, 이 포트를
    가짜로 갈아끼우는 테스트들은 그 경로를 타지 않아 잡히지 않았다.
    """
    broker = _FakeBroker()

    await KafkaEventBus(broker).publish(Event(stream="t", key="k", payload={"a": 1}))

    assert broker.connects == 1
    assert broker.published[0]["topic"] == "t"


async def test_already_connected_broker_is_not_reconnected() -> None:
    broker = _FakeBroker(running=True)

    await KafkaEventBus(broker).publish(Event(stream="t", key="k", payload={}))

    assert broker.connects == 0


async def test_concurrent_publishes_connect_once() -> None:
    # 요청마다 새 KafkaEventBus가 만들어지므로(get_event_bus), 동시 발행이 연결을
    # 여러 번 시도하지 않아야 한다.
    import asyncio

    broker = _FakeBroker()

    await asyncio.gather(
        *[
            KafkaEventBus(broker).publish(Event(stream="t", key="k", payload={}))
            for _ in range(5)
        ]
    )

    assert broker.connects == 1
    assert len(broker.published) == 5
