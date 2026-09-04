"""idle 진행 — 자율발화·사연 낭독 (FR-IDLE-1·2·3).

이 단위의 위험은 셋이다:

1. **사연이 헛되이 소비되는 것.** `claim_next_pending`은 조회가 아니라 상태 전이라,
   claim해 놓고 발행하지 못하면 시청자가 남긴 사연이 읽히지도 않고 사라진다.
2. **중복 발화.** 워커가 여럿이면 같은 방에 두 번 말을 건다.
3. **다시 집기.** 진행시킨 방을 다음 틱이 또 idle로 보면 계속 말한다.
"""

from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import RecordingEventBus
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from aria.common.story_feed import PendingStory
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.idle_lock import RedisIdleLock
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.idle import IdleProgressService
from aria.contexts.chat.domain.source import ChatSource
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


def _service(
    redis: FakeAsyncRedis, stories: _FakeStories, bus: object | None = None
) -> IdleProgressService:
    return IdleProgressService(
        activity=RedisActivityTracker(redis),
        lock=RedisIdleLock(redis),
        stories=stories,
        generation=GenerationRequestPublisher(bus or RecordingEventBus()),
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

    assert await _service(redis, stories).advance(room, uuid4()) is False
    # 사연을 건드리지도 않았다 — idle이 아니면 claim까지 가지 않는다.
    assert stories.claims == 0


async def test_idle_room_with_a_story_reads_it(redis: FakeAsyncRedis) -> None:
    events = RecordingEventBus()
    story = _story(nickname="복숭아")
    service = _service(redis, _FakeStories(story), events)

    assert await service.advance(uuid4(), uuid4()) is True

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

    assert await _service(redis, stories).advance(room, uuid4()) is False
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

    assert await service.advance(room, uuid4()) is True
    assert await service.advance(room, uuid4()) is False
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
        self._rooms = [_StubRoom(rid, pid) for rid, pid in rooms]
        self.asked_limit: int | None = None

    async def list_live(self, *, limit: int = 20, offset: int = 0):
        self.asked_limit = limit
        return self._rooms[:limit]


class _StubRoom:
    def __init__(self, room_id: UUID, persona_id: UUID) -> None:
        self.id = room_id
        self.persona_id = persona_id


async def test_tick_advances_every_idle_room(redis: FakeAsyncRedis) -> None:
    from aria.workers.idle import tick

    rooms = _StubRooms((uuid4(), uuid4()), (uuid4(), uuid4()))
    events = RecordingEventBus()

    assert await tick(rooms, _service(redis, _FakeStories(), events)) == 2
    assert len(events.published) == 2


async def test_one_failing_room_does_not_stop_the_others(
    redis: FakeAsyncRedis,
) -> None:
    """방 하나가 터져도 나머지 방송은 계속 진행돼야 한다."""
    from aria.workers.idle import tick

    doomed, healthy = uuid4(), uuid4()
    events = RecordingEventBus()

    class _FlakyService(IdleProgressService):
        async def advance(self, room_id: UUID, persona_id: UUID) -> bool:
            if room_id == doomed:
                raise RuntimeError("이 방만 터진다")
            return await super().advance(room_id, persona_id)

    service = _FlakyService(
        activity=RedisActivityTracker(redis),
        lock=RedisIdleLock(redis),
        stories=_FakeStories(),
        generation=GenerationRequestPublisher(events),
        threshold_seconds=_THRESHOLD,
    )

    advanced = await tick(_StubRooms((doomed, uuid4()), (healthy, uuid4())), service)

    assert advanced == 1
    assert len(events.published) == 1


async def test_tick_asks_for_at_most_the_configured_number_of_rooms(
    redis: FakeAsyncRedis,
) -> None:
    # 병렬화·샤딩은 보류하고 상한만 둔다 — 부하가 실측되기 전이기 때문이다.
    from aria.common.config import settings
    from aria.workers.idle import tick

    rooms = _StubRooms((uuid4(), uuid4()))
    await tick(rooms, _service(redis, _FakeStories()))

    assert rooms.asked_limit == settings.idle_rooms_per_tick
