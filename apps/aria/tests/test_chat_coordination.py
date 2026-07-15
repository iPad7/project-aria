from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis

from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.domain.source import ChatSource


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(decode_responses=True)


# --- ResponseCoordinator: 우선순위 단일-플라이트 + 펜싱 ---


async def test_acquire_on_free_room(redis: FakeAsyncRedis) -> None:
    coord = RedisResponseCoordinator(redis)
    slot = await coord.try_acquire(uuid4(), ChatSource.CHAT)
    assert slot is not None
    assert slot.source is ChatSource.CHAT


async def test_same_or_lower_priority_cannot_acquire_while_held(
    redis: FakeAsyncRedis,
) -> None:
    coord = RedisResponseCoordinator(redis)
    room = uuid4()
    assert await coord.try_acquire(room, ChatSource.CHAT) is not None
    assert await coord.try_acquire(room, ChatSource.CHAT) is None  # 같은 우선순위
    assert await coord.try_acquire(room, ChatSource.IDLE) is None  # 더 낮은 우선순위


async def test_higher_priority_preempts(redis: FakeAsyncRedis) -> None:
    coord = RedisResponseCoordinator(redis)
    room = uuid4()
    await coord.try_acquire(room, ChatSource.CHAT)
    assert await coord.try_acquire(room, ChatSource.SUPERCHAT) is not None


async def test_stale_release_does_not_free_preemptor(redis: FakeAsyncRedis) -> None:
    coord = RedisResponseCoordinator(redis)
    room = uuid4()
    chat_slot = await coord.try_acquire(room, ChatSource.CHAT)
    assert chat_slot is not None
    await coord.try_acquire(room, ChatSource.SUPERCHAT)  # chat을 선점

    await coord.release(
        room, chat_slot
    )  # 뒤늦은 stale release — 남의 락 건드리면 안 됨

    # superchat이 여전히 점유 중이어야 한다
    assert await coord.try_acquire(room, ChatSource.CHAT) is None


async def test_release_frees_lock(redis: FakeAsyncRedis) -> None:
    coord = RedisResponseCoordinator(redis)
    room = uuid4()
    slot = await coord.try_acquire(room, ChatSource.CHAT)
    assert slot is not None
    await coord.release(room, slot)
    assert await coord.try_acquire(room, ChatSource.CHAT) is not None


# --- ActivityTracker: idle 외부화 ---


async def test_idle_when_untouched(redis: FakeAsyncRedis) -> None:
    tracker = RedisActivityTracker(redis)
    assert await tracker.is_idle(uuid4(), 6.0) is True
    assert await tracker.seconds_since_last(uuid4()) is None


async def test_not_idle_after_touch(redis: FakeAsyncRedis) -> None:
    tracker = RedisActivityTracker(redis)
    room = uuid4()
    await tracker.touch(room)
    assert await tracker.is_idle(room, 6.0) is False
    elapsed = await tracker.seconds_since_last(room)
    assert elapsed is not None and elapsed < 6.0
