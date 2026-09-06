"""진행 — 댓글 선별·사연 낭독·자율발화 (FR-GEN-1·2, FR-IDLE-1·2·3).

이 단위의 위험은 다섯이다:

1. **사연이 헛되이 소비되는 것.** `claim_next_pending`은 조회가 아니라 상태 전이라,
   claim해 놓고 발행하지 못하면 시청자가 남긴 사연이 읽히지도 않고 사라진다.
2. **중복 발화.** 워커가 여럿이면 같은 방에 두 번 말을 건다.
3. **다시 집기.** 진행시킨 방을 다음 틱이 또 집으면 계속 말한다.
4. **우선순위 뒤집힘.** 시청자가 말을 걸고 있는데 혼잣말을 하는 것.
5. **빈 방에서의 발화.** 아무도 보지 않는 방에 계속 말을 걸면 비용만 나간다.

대부분의 테스트는 "방송 중이고 보는 사람이 있는" 상황을 다루므로 시청자 수를
`_Watched` 로 고정한다. 시청자 자체가 주제인 테스트에서는 **진짜 구독으로** 만든다 —
어댑터가 방 채널의 pub/sub 구독자 수를 그대로 세므로, 거기서도 스텁을 꽂으면 정작
그 계산을 검증하지 못한다.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import RecordingEventBus
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from aria.common.story_feed import PendingStory
from aria.contexts.chat.adapter.outbound.clustering.lexical import (
    LexicalTopicClusterer,
)
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.audience import RedisRoomAudience
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.adapter.outbound.redis.idle_lock import RedisIdleLock
from aria.contexts.chat.application.abandon import AbandonedRoomCloser
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.port.out.candidates import CandidateBuffer
from aria.contexts.chat.application.progress import ProgressService
from aria.contexts.chat.domain.room import Room, RoomStatus
from aria.contexts.chat.domain.source import ChatSource
from aria.contexts.chat.domain.topic import Candidate
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.domain.model import Story, StoryStatus

_THRESHOLD = 6.0


class _FakeStories:
    """`StoryFeedPort` 스텁 — claim이 **소비**라는 성질을 그대로 흉내 낸다."""

    def __init__(self, *stories: PendingStory) -> None:
        self._queue = list(stories)
        self.claims = 0
        self.done: list[UUID] = []
        self.released: list[UUID] = []

    async def claim_next_pending(self, persona_id: UUID) -> PendingStory | None:
        self.claims += 1
        return self._queue.pop(0) if self._queue else None

    async def mark_done(self, story_id: UUID) -> None:
        self.done.append(story_id)

    async def release(self, story_id: UUID) -> None:
        self.released.append(story_id)
        self._queue.insert(0, PendingStory(story_id, uuid4(), "되돌린 사연", "내용"))


class _BrokenBus:
    async def publish(self, event: object) -> None:
        raise ConnectionError("브로커 다운")


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer(), decode_responses=True)


class _MemoryCandidates:
    """`CandidateBuffer` 스텁 — take_all 이 **소비**라는 성질을 흉내 낸다."""

    def __init__(self, *candidates: Candidate) -> None:
        self._by_room: dict[UUID, list[Candidate]] = {}
        self._initial = list(candidates)

    async def add(self, room_id: UUID, candidate: Candidate) -> None:
        self._by_room.setdefault(room_id, []).append(candidate)

    async def take_all(self, room_id: UUID) -> list[Candidate]:
        taken = self._by_room.pop(room_id, None)
        if taken is None and self._initial:
            taken, self._initial = self._initial, []
        return taken or []


def _candidate(text: str) -> Candidate:
    return Candidate(message_id=uuid4(), author_id=uuid4(), text=text)


class _Watched:
    """누군가 보고 있는 방. 시청자가 주제가 아닌 테스트의 기본값이다."""

    async def viewer_count(self, room_id: UUID) -> int:
        return 1


class _Empty:
    """아무도 보지 않는 방."""

    async def viewer_count(self, room_id: UUID) -> int:
        return 0


def _service(
    redis: FakeAsyncRedis,
    stories: _FakeStories,
    bus: object | None = None,
    candidates: CandidateBuffer | None = None,
    audience: object | None = None,
) -> ProgressService:
    return ProgressService(
        activity=RedisActivityTracker(redis),
        lock=RedisIdleLock(redis),
        stories=stories,
        generation=GenerationRequestPublisher(bus or RecordingEventBus()),
        candidates=candidates or _MemoryCandidates(),
        clusterer=LexicalTopicClusterer(),
        coordinator=RedisResponseCoordinator(redis),
        audience=audience or _Watched(),
        threshold_seconds=_THRESHOLD,
    )


def _story(nickname: str | None = None) -> PendingStory:
    return PendingStory(
        story_id=uuid4(),
        persona_id=uuid4(),
        title="짝사랑 중입니다",
        content="3년째 같은 사람을 좋아하고 있어요.",
        nickname=nickname,
    )


# --- 언제 진행하는가 --------------------------------------------------------


async def test_active_room_is_left_alone(redis: FakeAsyncRedis) -> None:
    room = uuid4()
    await RedisActivityTracker(redis).touch(room)  # 방금 누가 말했다
    stories = _FakeStories(_story())

    assert await _service(redis, stories).advance(room, uuid4()) is None
    # 사연을 건드리지도 않았다 — idle이 아니면 claim까지 가지 않는다.
    assert stories.claims == 0


async def test_idle_room_with_a_story_reads_it(redis: FakeAsyncRedis) -> None:
    events = RecordingEventBus()
    story = _story(nickname="복숭아")
    service = _service(redis, _FakeStories(story), events)

    assert await service.advance(uuid4(), uuid4()) is not None

    [event] = events.published
    assert event.payload["source"] == ChatSource.STORY.value
    # 낭독에 필요한 것이 프롬프트에 실린다.
    assert "복숭아" in event.payload["prompt"]
    assert "짝사랑 중입니다" in event.payload["prompt"]
    assert "3년째" in event.payload["prompt"]


async def test_idle_room_without_stories_talks_to_itself(
    redis: FakeAsyncRedis,
) -> None:
    events = RecordingEventBus()

    assert await _service(redis, _FakeStories(), events).advance(uuid4(), uuid4())

    [event] = events.published
    assert event.payload["source"] == ChatSource.IDLE.value


async def test_story_wins_over_self_talk(redis: FakeAsyncRedis) -> None:
    # 사연은 시청자가 실제로 남긴 것이고 자율발화는 빈자리를 메우는 것이다.
    events = RecordingEventBus()
    service = _service(redis, _FakeStories(_story()), events)

    await service.advance(uuid4(), uuid4())

    assert events.published[0].payload["source"] == ChatSource.STORY.value


async def test_anonymous_story_is_read_without_a_name(redis: FakeAsyncRedis) -> None:
    events = RecordingEventBus()
    await _service(redis, _FakeStories(_story()), events).advance(uuid4(), uuid4())

    assert "한 시청자가" in events.published[0].payload["prompt"]


# --- 사연이 헛되이 소비되지 않는가 (이 단위의 핵심) --------------------------


async def test_locked_room_does_not_touch_stories(redis: FakeAsyncRedis) -> None:
    """락이 사연 claim보다 **앞**이어야 하는 이유.

    코디네이터만으로도 중복 발화는 막히지만, 그때는 이미 사연이 `reading`으로
    빠져나간 뒤다 — 슬롯을 못 잡은 쪽의 사연은 읽히지도 않고 사라진다.
    """
    room = uuid4()
    stories = _FakeStories(_story())
    await RedisIdleLock(redis).acquire(room)  # 다른 워커가 이미 맡았다

    assert await _service(redis, stories).advance(room, uuid4()) is None
    assert stories.claims == 0  # 사연을 건드리지 않았다


async def test_publish_failure_returns_the_story_to_the_queue(
    redis: FakeAsyncRedis,
) -> None:
    # 되돌리지 않으면 그 사연은 영원히 `reading`에 갇힌다.
    story = _story()
    stories = _FakeStories(story)
    service = _service(redis, stories, _BrokenBus())

    with pytest.raises(ConnectionError):
        await service.advance(uuid4(), uuid4())

    assert stories.released == [story.story_id]
    assert stories.done == []


async def test_successful_dispatch_marks_the_story_done(
    redis: FakeAsyncRedis,
) -> None:
    story = _story()
    stories = _FakeStories(story)

    await _service(redis, stories).advance(uuid4(), uuid4())

    assert stories.done == [story.story_id]


async def test_lock_is_released_even_when_publishing_fails(
    redis: FakeAsyncRedis,
) -> None:
    # 놓지 않으면 그 방은 락 TTL이 지날 때까지 조용해진다.
    room = uuid4()
    with pytest.raises(ConnectionError):
        await _service(redis, _FakeStories(), _BrokenBus()).advance(room, uuid4())

    assert await RedisIdleLock(redis).acquire(room) is True


# --- 다시 집지 않는가 -------------------------------------------------------


async def test_advancing_marks_the_room_active(redis: FakeAsyncRedis) -> None:
    # 이게 없으면 생성이 끝나기 전에 다음 틱이 또 idle로 보고 계속 말을 건다.
    room = uuid4()
    stories = _FakeStories(_story(), _story())
    service = _service(redis, stories)

    assert await service.advance(room, uuid4()) is not None
    assert await service.advance(room, uuid4()) is None
    assert stories.claims == 1


# --- 방별 락 어댑터 ---------------------------------------------------------


async def test_idle_lock_is_exclusive_per_room(redis: FakeAsyncRedis) -> None:
    lock = RedisIdleLock(redis)
    room = uuid4()

    assert await lock.acquire(room) is True
    assert await lock.acquire(room) is False
    assert await lock.acquire(uuid4()) is True  # 다른 방은 영향 없다

    await lock.release(room)
    assert await lock.acquire(room) is True


# --- 사연 상태 되돌리기 (community 구현) ------------------------------------


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _persist(session: Session, status: StoryStatus) -> Story:
    story = Story(
        persona_id=uuid4(),
        author_id=uuid4(),
        title="사연",
        content="내용",
        status=status,
    )
    repo = SqlModelStoryRepository(session)
    repo.add(story)
    if status is not StoryStatus.PENDING:
        # add는 pending으로 넣으므로 원하는 상태로 옮긴다.
        from aria.contexts.community.adapter.outbound.persistence.model import (
            StoryTable,
        )

        row = session.get(StoryTable, story.id)
        assert row is not None
        row.status = status.value
        session.add(row)
        session.commit()
    return story


def test_release_returns_a_reading_story_to_pending(session: Session) -> None:
    repo = SqlModelStoryRepository(session)
    story = _persist(session, StoryStatus.READING)

    repo.release(story.id)

    assert repo.get_by_id(story.id).status is StoryStatus.PENDING


def test_release_does_not_resurrect_a_finished_story(session: Session) -> None:
    # 읽고 나서 뒤늦게 도착한 실패 처리가 끝난 사연을 대기열에 다시 넣으면 두 번 읽는다.
    repo = SqlModelStoryRepository(session)
    story = _persist(session, StoryStatus.DONE)

    repo.release(story.id)

    assert repo.get_by_id(story.id).status is StoryStatus.DONE


def test_release_of_unknown_story_is_a_no_op(session: Session) -> None:
    SqlModelStoryRepository(session).release(uuid4())  # 예외가 없으면 통과


def test_released_story_can_be_claimed_again(session: Session) -> None:
    repo = SqlModelStoryRepository(session)
    story = _persist(session, StoryStatus.READING)

    repo.release(story.id)

    claimed = repo.claim_next_pending(story.persona_id)
    assert claimed is not None and claimed.id == story.id


# --- 루프 한 틱 (워커) ------------------------------------------------------


class _StubRooms:
    """`RoomService.list_live()` 자리에 꽂는 스텁."""

    def __init__(self, *rooms: tuple[UUID, UUID]) -> None:
        self._rooms = [_room(rid, pid) for rid, pid in rooms]
        self.asked_limit: int | None = None

    async def list_live(self, *, limit: int = 20, offset: int = 0):
        self.asked_limit = limit
        return self._rooms[:limit]


def _room(room_id: UUID, persona_id: UUID) -> Room:
    return Room(
        id=room_id,
        persona_id=persona_id,
        host_id=uuid4(),
        name="방송",
        status=RoomStatus.LIVE,
    )


class _NeverCloses:
    """정리를 하지 않는 정리자 — 진행만 보는 테스트에 꽂는다."""

    async def close_if_abandoned(self, room: Room) -> bool:
        return False


async def test_tick_advances_every_idle_room(redis: FakeAsyncRedis) -> None:
    from aria.workers.idle import tick

    rooms = _StubRooms((uuid4(), uuid4()), (uuid4(), uuid4()))
    events = RecordingEventBus()

    advanced = await tick(
        rooms, _service(redis, _FakeStories(), events), _NeverCloses()
    )

    assert advanced == 2
    assert len(events.published) == 2


async def test_one_failing_room_does_not_stop_the_others(
    redis: FakeAsyncRedis,
) -> None:
    """방 하나가 터져도 나머지 방송은 계속 진행돼야 한다."""
    from aria.workers.idle import tick

    doomed, healthy = uuid4(), uuid4()
    events = RecordingEventBus()

    class _FlakyService(ProgressService):
        async def advance(self, room_id: UUID, persona_id: UUID):
            if room_id == doomed:
                raise RuntimeError("이 방만 터진다")
            return await super().advance(room_id, persona_id)

    service = _FlakyService(
        activity=RedisActivityTracker(redis),
        lock=RedisIdleLock(redis),
        stories=_FakeStories(),
        generation=GenerationRequestPublisher(events),
        candidates=_MemoryCandidates(),
        clusterer=LexicalTopicClusterer(),
        coordinator=RedisResponseCoordinator(redis),
        audience=_Watched(),
        threshold_seconds=_THRESHOLD,
    )

    advanced = await tick(
        _StubRooms((doomed, uuid4()), (healthy, uuid4())), service, _NeverCloses()
    )

    assert advanced == 1
    assert len(events.published) == 1


async def test_tick_asks_for_at_most_the_configured_number_of_rooms(
    redis: FakeAsyncRedis,
) -> None:
    # 병렬화·샤딩은 보류하고 상한만 둔다 — 부하가 실측되기 전이기 때문이다.
    from aria.common.config import settings
    from aria.workers.idle import tick

    rooms = _StubRooms((uuid4(), uuid4()))
    await tick(rooms, _service(redis, _FakeStories()), _NeverCloses())

    assert rooms.asked_limit == settings.idle_rooms_per_tick


# --- 우선순위: 댓글 > 사연 > 자율발화 ----------------------------------------
#
# 셋은 경쟁 관계다 — 한 방에서 하나만 말할 수 있으므로 한 곳에서 골라야 한다.
# 나누면 둘이 각자 발행하고 코디네이터가 하나를 버리는 낭비가 생긴다.


async def test_chat_wins_over_a_waiting_story(redis: FakeAsyncRedis) -> None:
    """시청자가 지금 말을 걸고 있는데 사연을 읽으면 이상하다."""
    events = RecordingEventBus()
    stories = _FakeStories(_story())
    service = _service(
        redis,
        stories,
        events,
        candidates=_MemoryCandidates(_candidate("고백하는 게 맞을까요?")),
    )

    progress = await service.advance(uuid4(), uuid4())

    assert progress is not None
    assert progress.source is ChatSource.CHAT
    assert events.published[0].payload["source"] == "chat"
    # 사연은 건드리지 않았다 — claim 은 소비라 되돌릴 수 없다.
    assert stories.claims == 0


async def test_story_is_read_when_chat_is_quiet(redis: FakeAsyncRedis) -> None:
    events = RecordingEventBus()

    progress = await _service(redis, _FakeStories(_story()), events).advance(
        uuid4(), uuid4()
    )

    assert progress is not None and progress.source is ChatSource.STORY


async def test_self_talk_when_nothing_else_is_pending(
    redis: FakeAsyncRedis,
) -> None:
    progress = await _service(redis, _FakeStories()).advance(uuid4(), uuid4())

    assert progress is not None and progress.source is ChatSource.IDLE


async def test_chat_is_answered_even_when_the_room_is_not_idle(
    redis: FakeAsyncRedis,
) -> None:
    """활동 중인 방도 답해야 한다 — idle 판정은 사연·자율발화에만 건다.

    이게 없으면 채팅이 활발한 방이 영영 답을 못 받는다(항상 not idle 이므로).
    """
    room = uuid4()
    await RedisActivityTracker(redis).touch(room)  # 방금 누가 말했다
    service = _service(
        redis,
        _FakeStories(_story()),
        candidates=_MemoryCandidates(_candidate("고백하는 게 맞을까요?")),
    )

    progress = await service.advance(room, uuid4())

    assert progress is not None and progress.source is ChatSource.CHAT


async def test_selection_details_are_reported(redis: FakeAsyncRedis) -> None:
    # 트레이스에 실을 근거다 — "후보 몇 개 중 왜 저걸 골랐나".
    service = _service(
        redis,
        _FakeStories(),
        candidates=_MemoryCandidates(
            _candidate("고백하는 게 맞을까요?"),
            _candidate("ㅋㅋ"),
        ),
    )

    progress = await service.advance(uuid4(), uuid4())

    assert progress is not None
    assert progress.candidate_count == 2
    assert progress.topic_count >= 1
    assert progress.selection is not None
    assert "question" in progress.selection.reasons


async def test_locked_room_does_not_consume_candidates(
    redis: FakeAsyncRedis,
) -> None:
    # 후보를 꺼내는 것도 소비다 — 락이 그 앞에 있어야 한다.
    room = uuid4()
    candidates = _MemoryCandidates(_candidate("고백할까요?"))
    await RedisIdleLock(redis).acquire(room)

    assert (
        await _service(redis, _FakeStories(), candidates=candidates).advance(
            room, uuid4()
        )
        is None
    )
    # 소비되지 않았으므로 후보가 그대로 남아 있다 — 락을 놓으면 다음 틱이 답한다.
    assert len(await candidates.take_all(room)) == 1


# --- 헛된 소비를 막는가 (사후 발견 · 이번 단위가 남겼던 결함) -----------------


async def test_busy_room_does_not_consume_candidates(redis: FakeAsyncRedis) -> None:
    """생성 워커가 슬롯을 못 잡으면 그 배치의 댓글이 답도 없이 사라졌다.

    사연에는 같은 문제를 알고 `release` 를 뒀는데 후보에는 두지 않았다. 후보를 꺼내는
    것도 되돌릴 수 없는 소비이므로, **꺼내기 전에** 지금 누가 응답 중인지 묻는다.
    """
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    await coordinator.try_acquire(room, ChatSource.SUPERCHAT)  # 후원이 응답 중
    candidates = _MemoryCandidates(_candidate("고백하는 게 맞을까요?"))

    progress = await _service(redis, _FakeStories(), candidates=candidates).advance(
        room, uuid4()
    )

    assert progress is None
    # 후보가 그대로 남아 있다 — 슬롯이 비면 다음 틱이 답한다.
    assert len(await candidates.take_all(room)) == 1


async def test_busy_room_does_not_consume_a_story(redis: FakeAsyncRedis) -> None:
    # 같은 이유로 사연도 claim 하지 않는다.
    room = uuid4()
    await RedisResponseCoordinator(redis).try_acquire(room, ChatSource.SUPERCHAT)
    stories = _FakeStories(_story())

    assert await _service(redis, stories).advance(room, uuid4()) is None
    assert stories.claims == 0


async def test_free_room_proceeds(redis: FakeAsyncRedis) -> None:
    # 대조군 — 아무도 응답 중이 아니면 그대로 진행한다.
    progress = await _service(redis, _FakeStories(_story())).advance(uuid4(), uuid4())

    assert progress is not None


# --- 듣는 사람이 없으면 말하지 않는가 ---------------------------------------
#
# 방 16개가 정리되지 않은 채 남아 분당 160회씩 자율발화하고 있었다. 비용 이전에
# 제품이 이상하다 — 시청자가 0명인데 떠드는 스트리머는 없다.


async def test_empty_room_does_not_talk_to_itself(redis: FakeAsyncRedis) -> None:
    events = RecordingEventBus()
    service = _service(redis, _FakeStories(), events, audience=_Empty())

    assert await service.advance(uuid4(), uuid4()) is None
    assert events.published == []


async def test_empty_room_does_not_consume_a_story(redis: FakeAsyncRedis) -> None:
    """자율발화보다 이쪽이 더 나쁘다.

    낭독은 시청자가 남긴 사연을 `done`으로 소비하므로, 빈 방에서 읽으면 그 사연은
    아무에게도 닿지 못한 채 사라진다.
    """
    stories = _FakeStories(_story())

    assert (
        await _service(redis, stories, audience=_Empty()).advance(uuid4(), uuid4())
        is None
    )
    assert stories.claims == 0


async def test_empty_room_still_answers_comments(redis: FakeAsyncRedis) -> None:
    """댓글은 청한 사람이 있다 — 지금 보고 있지 않아도 답한다.

    끊었다 재접속하는 순간이 있고, 남긴 댓글이 답을 못 받고 사라지면 그건 유실이다.
    """
    service = _service(
        redis,
        _FakeStories(),
        candidates=_MemoryCandidates(_candidate("고백하는 게 맞을까요?")),
        audience=_Empty(),
    )

    progress = await service.advance(uuid4(), uuid4())

    assert progress is not None and progress.source is ChatSource.CHAT


async def test_viewer_count_is_the_room_channel_subscriber_count(
    redis: FakeAsyncRedis,
) -> None:
    """따로 명부를 두지 않는 이유 — 구독 수가 이미 정확한 답이다.

    heartbeat 방식이면 프로세스가 죽을 때 유령 시청자가 남는데, 그건 이 단위가
    없애려는 문제와 같은 종류다.
    """
    audience = RedisRoomAudience(redis)
    room, other = uuid4(), uuid4()

    assert await audience.viewer_count(room) == 0

    broadcaster = RedisRoomBroadcaster(redis)
    stream = await broadcaster.subscribe(room)
    assert await audience.viewer_count(room) == 1
    assert await audience.viewer_count(other) == 0  # 다른 방은 영향 없다

    # 세는 구독이 방송이 흐르는 그 구독임을 확인한다. 겸사겸사 스트림을 **시작**시킨다
    # — 시작하지 않은 async generator는 `aclose()`가 finally(구독 해제)를 돌리지 않는다.
    await broadcaster.publish(room, {"type": "ping"})
    assert await anext(stream) == {"type": "ping"}

    await stream.aclose()
    assert await audience.viewer_count(room) == 0  # 나가면 곧바로 0이다


async def test_a_watched_room_keeps_talking(redis: FakeAsyncRedis) -> None:
    # 대조군 — 진짜 구독이 하나 있으면 그전과 똑같이 자율발화한다.
    room = uuid4()
    stream = await RedisRoomBroadcaster(redis).subscribe(room)
    service = _service(redis, _FakeStories(), audience=RedisRoomAudience(redis))

    progress = await service.advance(room, uuid4())

    assert progress is not None and progress.source is ChatSource.IDLE
    await stream.aclose()


# --- 방치된 방은 스스로 끝나는가 --------------------------------------------
#
# 방을 여는 사람은 있는데 끝내는 사람이 아무도 없었다. 시청자 검사로 비용은 멈추지만,
# 방은 여전히 목록에 남고 그 페르소나는 새 방송을 열지 못한다(live 부분 유일 인덱스).


class _RecordingRooms:
    """`RoomService.transition()` 만 쓰는 정리자에 꽂는 스텁."""

    def __init__(self) -> None:
        self.finished: list[UUID] = []

    async def transition(self, room_id: UUID, status: RoomStatus):
        assert status is RoomStatus.FINISHED
        self.finished.append(room_id)


def _closer(
    redis: FakeAsyncRedis,
    rooms: _RecordingRooms,
    audience: object,
    *,
    abandon_seconds: float = 1800.0,
) -> AbandonedRoomCloser:
    return AbandonedRoomCloser(
        rooms,  # type: ignore[arg-type]
        RedisActivityTracker(redis),
        audience,  # type: ignore[arg-type]
        abandon_seconds=abandon_seconds,
    )


async def test_a_long_silent_empty_room_is_closed(redis: FakeAsyncRedis) -> None:
    rooms = _RecordingRooms()
    room = _room(uuid4(), uuid4())
    room.created_at = datetime.now(UTC) - timedelta(hours=2)

    assert await _closer(redis, rooms, _Empty()).close_if_abandoned(room) is True
    assert rooms.finished == [room.id]


async def test_a_watched_room_is_never_closed(redis: FakeAsyncRedis) -> None:
    """침묵만 보면 위험하다.

    생성이 계속 실패해 진행이 성사되지 않는 방은 사람이 보고 있어도 조용해 보인다 —
    그러면 시청자 앞에서 방송이 꺼진다.
    """
    rooms = _RecordingRooms()
    room = _room(uuid4(), uuid4())
    room.created_at = datetime.now(UTC) - timedelta(days=1)

    assert await _closer(redis, rooms, _Watched()).close_if_abandoned(room) is False
    assert rooms.finished == []


async def test_a_fresh_room_is_not_closed(redis: FakeAsyncRedis) -> None:
    """활동 기록이 아직 없는 방을 "오래 조용했다"고 오해하면 안 된다.

    개설 직후에는 아무도 말하지 않았으므로 활동 키가 없다 — 그것을 침묵 무한대로
    읽으면 열자마자 닫힌다.
    """
    rooms = _RecordingRooms()

    closed = await _closer(redis, rooms, _Empty()).close_if_abandoned(
        _room(uuid4(), uuid4())
    )

    assert closed is False
    assert rooms.finished == []


async def test_recent_activity_keeps_an_empty_room_open(redis: FakeAsyncRedis) -> None:
    # 방금까지 대화가 있었다면 잠깐 아무도 없어도 닫지 않는다.
    rooms = _RecordingRooms()
    room = _room(uuid4(), uuid4())
    room.created_at = datetime.now(UTC) - timedelta(days=1)
    await RedisActivityTracker(redis).touch(room.id)

    assert await _closer(redis, rooms, _Empty()).close_if_abandoned(room) is False


async def test_closing_a_room_stops_the_tick_from_advancing_it(
    redis: FakeAsyncRedis,
) -> None:
    # 정리가 진행보다 앞이어야 하는 이유 — 방금 끝낸 방에 같은 틱이 또 말을 건다.
    from aria.workers.idle import tick

    room_id = uuid4()
    rooms = _StubRooms((room_id, uuid4()))
    rooms._rooms[0].created_at = datetime.now(UTC) - timedelta(hours=2)
    events = RecordingEventBus()

    advanced = await tick(
        rooms,
        _service(redis, _FakeStories(), events),
        _closer(redis, _RecordingRooms(), _Empty()),
    )

    assert advanced == 0
    assert events.published == []
