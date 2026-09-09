"""방 기록 — 오간 말이 남고, 되읽히고, 응답이 무엇에 답했는지가 이어진다.

**이 파일이 지키는 것 셋.**

1. **기록 실패가 방송을 끊지 않는다.** 기록은 부가 기능이다 — DB가 흔들렸다고 시청자의
   메시지가 거부되거나 후원이 실패하면 안 된다. 반대로 조용히 없어져서도 안 되므로
   로그에는 남는다.
2. **응답은 발행보다 먼저 남는다.** 순서가 뒤집히면 시청자가 이미 본 말이 히스토리에
   없다 — 새로고침하면 사라지는 응답이 된다.
3. **`replied_to`가 학습 쌍의 연결선이다.** 이 응답이 어느 시청자 메시지에 답한
   것인지. 자율발화·사연 낭독은 비어 있고, 그 구분이 데이터셋을 거를 때 필요하다.

커서 페이징을 오프셋 대신 쓰는 이유도 여기서 본다 — 방송 중에는 새 메시지가 계속
들어와 오프셋이 밀린다.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from aria.contexts.chat.adapter.outbound.persistence.transcript import (
    SqlModelTranscriptRepository,
)
from aria.contexts.chat.domain.message import MessageKind, RoomMessage
from aria.contexts.chat.domain.source import ChatSource

# --- 하네스 ----------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def transcript(session: Session) -> SqlModelTranscriptRepository:
    return SqlModelTranscriptRepository(session)


@pytest.fixture
def room() -> UUID:
    return uuid4()


# --- 도메인: 종류마다 채워지는 것이 다르다 ----------------------------------


def test_a_reply_has_no_viewer_author() -> None:
    """응답에 시청자를 저자로 달면 "누가 말했나"가 무너진다."""
    with pytest.raises(ValueError, match="author_id"):
        RoomMessage(
            room_id=uuid4(),
            kind=MessageKind.REPLY,
            text="안녕하세요",
            author_id=uuid4(),
        )


def test_only_a_reply_can_point_at_what_it_answered() -> None:
    with pytest.raises(ValueError, match="replied_to"):
        RoomMessage(
            room_id=uuid4(),
            kind=MessageKind.CHAT,
            text="안녕",
            author_id=uuid4(),
            replied_to=uuid4(),
        )


def test_only_a_superchat_carries_an_amount() -> None:
    with pytest.raises(ValueError, match="amount"):
        RoomMessage(
            room_id=uuid4(),
            kind=MessageKind.CHAT,
            text="안녕",
            author_id=uuid4(),
            amount=100,
        )


def test_a_superchat_may_have_no_message() -> None:
    """후원은 메시지 없이도 성립한다 — 빈 텍스트가 정상이다."""
    message = RoomMessage.from_superchat(uuid4(), uuid4(), uuid4(), 100, None)
    assert message.text == ""


def test_a_viewer_message_cannot_be_empty() -> None:
    with pytest.raises(ValueError):
        RoomMessage.from_viewer(uuid4(), uuid4(), "   ")


def test_a_viewer_message_is_capped_shorter_than_a_reply() -> None:
    """시청자 입력과 LLM 출력의 상한이 다르다 — 응답은 더 길 수 있다."""
    with pytest.raises(ValueError, match="500"):
        RoomMessage.from_viewer(uuid4(), uuid4(), "가" * 501)

    long_reply = RoomMessage.from_persona(
        uuid4(), uuid4(), ChatSource.IDLE, "가" * 1500
    )
    assert len(long_reply.text) == 1500


# --- 영속: 남고, 순서대로 되읽힌다 ------------------------------------------


async def test_the_timeline_holds_all_three_kinds(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    """한 표에 셋이 함께 산다 — 화면에서도 학습에서도 하나의 순서다."""
    await transcript.append(RoomMessage.from_viewer(room, uuid4(), "안녕하세요"))
    await transcript.append(
        RoomMessage.from_superchat(room, uuid4(), uuid4(), 500, "응원해요")
    )
    await transcript.append(
        RoomMessage.from_persona(room, uuid4(), ChatSource.CHAT, "고맙습니다")
    )

    kinds = [m.kind for m in await transcript.list_recent(room)]
    # 최신순이므로 넣은 역순이다.
    assert kinds == [MessageKind.REPLY, MessageKind.SUPERCHAT, MessageKind.CHAT]


async def test_another_room_is_not_mixed_in(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    await transcript.append(RoomMessage.from_viewer(room, uuid4(), "이 방"))
    await transcript.append(RoomMessage.from_viewer(uuid4(), uuid4(), "다른 방"))

    assert [m.text for m in await transcript.list_recent(room)] == ["이 방"]


async def test_a_reply_keeps_what_it_answered(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    """학습 쌍의 연결선이 왕복에서 살아남는다."""
    asked = RoomMessage.from_viewer(room, uuid4(), "연애 상담 해주세요")
    await transcript.append(asked)
    await transcript.append(
        RoomMessage.from_persona(
            room,
            uuid4(),
            ChatSource.CHAT,
            "그럼요",
            model_version="stub-1",
            replied_to=asked.id,
        )
    )

    reply = (await transcript.list_recent(room))[0]
    assert reply.replied_to == asked.id
    assert reply.model_version == "stub-1"


async def test_an_idle_utterance_answers_nothing(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    """자율발화는 답할 대상이 없다 — 데이터셋에서 걸러야 하는 쪽이다."""
    await transcript.append(
        RoomMessage.from_persona(room, uuid4(), ChatSource.IDLE, "오늘 날씨가 좋네요")
    )

    assert (await transcript.list_recent(room))[0].replied_to is None


# --- 커서 페이징 ------------------------------------------------------------


async def test_paging_walks_backwards_without_repeating(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    for i in range(5):
        await transcript.append(RoomMessage.from_viewer(room, uuid4(), f"메시지 {i}"))

    first = await transcript.list_recent(room, limit=2)
    second = await transcript.list_recent(room, limit=2, before=first[-1].id)

    assert [m.text for m in first] == ["메시지 4", "메시지 3"]
    assert [m.text for m in second] == ["메시지 2", "메시지 1"]


async def test_a_new_message_does_not_shift_the_next_page(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    """오프셋 페이징이었다면 여기서 같은 줄을 두 번 보게 된다.

    방송 중에는 새 메시지가 계속 들어온다 — 커서를 쓰는 이유가 이것이다.
    """
    for i in range(4):
        await transcript.append(RoomMessage.from_viewer(room, uuid4(), f"메시지 {i}"))
    first = await transcript.list_recent(room, limit=2)

    await transcript.append(RoomMessage.from_viewer(room, uuid4(), "방금 들어온 것"))

    second = await transcript.list_recent(room, limit=2, before=first[-1].id)
    assert [m.text for m in second] == ["메시지 1", "메시지 0"]


async def test_the_page_size_is_capped(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    for i in range(3):
        await transcript.append(RoomMessage.from_viewer(room, uuid4(), f"m{i}"))

    assert len(await transcript.list_recent(room, limit=1000)) == 3


# --- 유스케이스 배선: 남기되, 남기다 실패해도 방송은 계속된다 ----------------


class _FakeActivity:
    async def touch(self, room_id: UUID) -> None: ...
    async def seconds_since_last(self, room_id: UUID) -> float | None:
        return None

    async def is_idle(self, room_id: UUID, threshold: float) -> bool:
        return False


class _NullCandidates:
    async def add(self, room_id: UUID, candidate) -> None: ...
    async def take_all(self, room_id: UUID) -> list:
        return []


class _RecordingBroadcaster:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def publish(self, room_id: UUID, frame: dict) -> None:
        self.frames.append(frame)


class _BrokenTranscript:
    """append가 늘 실패한다 — DB가 흔들리는 상황."""

    async def append(self, message: RoomMessage) -> None:
        raise RuntimeError("DB가 죽었다")

    async def list_recent(self, room_id: UUID, *, limit: int = 50, before=None) -> list:
        return []


def _chat_service(transcript, broadcaster=None):
    from generation_harness import RecordingEventBus

    from aria.contexts.chat.application.generation import GenerationRequestPublisher
    from aria.contexts.chat.application.service import ChatOrchestrationService

    class _FakeSuperchat:
        async def charge(self, *args, **kwargs):
            from aria.common.superchat import SuperchatReceipt

            return SuperchatReceipt(donation_id=uuid4(), balance_after=0)

    return ChatOrchestrationService(
        activity=_FakeActivity(),
        broadcaster=broadcaster or _RecordingBroadcaster(),
        generation=GenerationRequestPublisher(RecordingEventBus()),
        superchat=_FakeSuperchat(),
        candidates=_NullCandidates(),
        transcript=transcript,
    )


async def test_a_viewer_message_is_recorded(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    await _chat_service(transcript).handle_user_message(
        room, uuid4(), uuid4(), "안녕하세요"
    )

    stored = await transcript.list_recent(room)
    assert [m.text for m in stored] == ["안녕하세요"]
    assert stored[0].kind is MessageKind.CHAT


async def test_a_superchat_is_recorded_with_its_amount(
    transcript: SqlModelTranscriptRepository, room: UUID
) -> None:
    await _chat_service(transcript).handle_superchat(
        room, uuid4(), uuid4(), 500, message="응원해요"
    )

    stored = (await transcript.list_recent(room))[0]
    assert stored.kind is MessageKind.SUPERCHAT
    assert stored.amount == 500


async def test_a_failed_recording_does_not_reject_the_message(
    room: UUID,
) -> None:
    """기록은 부가 기능이다 — DB가 흔들렸다고 시청자의 메시지를 거부하지 않는다."""
    broadcaster = _RecordingBroadcaster()

    outcome = await _chat_service(_BrokenTranscript(), broadcaster).handle_user_message(
        room, uuid4(), uuid4(), "안녕하세요"
    )

    assert outcome.accepted
    # 방송에는 그대로 나갔다.
    assert [f["type"] for f in broadcaster.frames] == ["message"]


async def test_a_failed_recording_does_not_void_a_superchat(room: UUID) -> None:
    """돈은 이미 빠졌다 — 기록 실패로 후원이 없던 일이 되면 안 된다."""
    broadcaster = _RecordingBroadcaster()

    outcome = await _chat_service(_BrokenTranscript(), broadcaster).handle_superchat(
        room, uuid4(), uuid4(), 500
    )

    assert outcome.balance_after == 0
    assert [f["type"] for f in broadcaster.frames] == ["superchat"]


# --- 응답: 발행보다 먼저 남는다 ---------------------------------------------


async def test_a_reply_is_recorded_before_it_is_broadcast(room: UUID) -> None:
    """순서가 뒤집히면 시청자가 이미 본 말이 히스토리에 없다.

    기록이 먼저면 최악은 "남았는데 못 나간 응답"이고 그건 조용하다. 반대는
    새로고침하면 사라지는 응답이라 눈에 보인다.
    """
    from fakeredis import FakeAsyncRedis, FakeServer
    from generation_harness import StubProfiles, StubRooms

    from aria.common.tracing import NoOpTracing
    from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
    from aria.contexts.chat.adapter.outbound.redis.coordinator import (
        RedisResponseCoordinator,
    )
    from aria.contexts.chat.application.generation import (
        GenerationRequest,
        ResponseGenerationService,
    )

    order: list[str] = []

    class _OrderedTranscript:
        async def append(self, message: RoomMessage) -> None:
            order.append("recorded")

        async def list_recent(self, room_id, *, limit=50, before=None) -> list:
            return []

    class _OrderedBroadcaster:
        async def publish(self, room_id: UUID, frame: dict) -> None:
            order.append("broadcast")

    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    await ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=StubPersonaLLM(),
        broadcaster=_OrderedBroadcaster(),
        profiles=StubProfiles(),
        tracing=NoOpTracing(),
        transcript=_OrderedTranscript(),
        rooms=StubRooms(),
    ).handle(GenerationRequest.create(room, uuid4(), ChatSource.IDLE, "무슨 말이든"))

    assert order == ["recorded", "broadcast"]


async def test_a_failed_recording_still_lets_the_persona_speak(room: UUID) -> None:
    from fakeredis import FakeAsyncRedis, FakeServer
    from generation_harness import StubProfiles, StubRooms

    from aria.common.tracing import NoOpTracing
    from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
    from aria.contexts.chat.adapter.outbound.redis.coordinator import (
        RedisResponseCoordinator,
    )
    from aria.contexts.chat.application.generation import (
        GenerationRequest,
        ResponseGenerationService,
    )

    broadcaster = _RecordingBroadcaster()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)

    await ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=StubPersonaLLM(),
        broadcaster=broadcaster,
        profiles=StubProfiles(),
        tracing=NoOpTracing(),
        transcript=_BrokenTranscript(),
        rooms=StubRooms(),
    ).handle(GenerationRequest.create(room, uuid4(), ChatSource.IDLE, "무슨 말이든"))

    assert [f["type"] for f in broadcaster.frames] == ["reply"]


async def test_the_selected_comment_becomes_replied_to(room: UUID) -> None:
    """#63의 선별 결과가 기록의 연결선이 된다 — 학습 쌍이 여기서 만들어진다."""
    from fakeredis import FakeAsyncRedis, FakeServer
    from generation_harness import RecordingTranscript, StubProfiles, StubRooms

    from aria.common.tracing import NoOpTracing
    from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
    from aria.contexts.chat.adapter.outbound.redis.coordinator import (
        RedisResponseCoordinator,
    )
    from aria.contexts.chat.application.generation import (
        GenerationRequest,
        ResponseGenerationService,
    )

    asked = uuid4()
    recorded = RecordingTranscript()
    redis = FakeAsyncRedis(server=FakeServer(), decode_responses=True)

    await ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=StubPersonaLLM(),
        broadcaster=_RecordingBroadcaster(),
        profiles=StubProfiles(),
        tracing=NoOpTracing(),
        transcript=recorded,
        rooms=StubRooms(),
    ).handle(
        GenerationRequest.create(
            room,
            uuid4(),
            ChatSource.CHAT,
            "연애 상담 해주세요",
            {"selected_message_id": str(asked), "selected_score": 0.9},
        )
    )

    assert recorded.appended[0].replied_to == asked
