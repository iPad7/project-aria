"""ResponseCoordinator의 Redis 구현 — 우선순위 단일-플라이트 락.

키 하나에 ``{priority}:{token}:{source}``를 담는다. WATCH/MULTI 낙관적 트랜잭션으로
compare-and-set 하여, 비어 있거나 더 높은 우선순위일 때만 잡는다(선점). release는
펜싱 토큰이 일치할 때만 지운다 — 선점당한 뒤 뒤늦게 release해도 남의 락을 건드리지 않는다.
락 홀더가 죽어도 TTL이 안전망.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError

from aria.contexts.chat.application.port.out.coordinator import ResponseSlot
from aria.contexts.chat.domain.source import ChatSource, priority_of

# 홀더가 응답 도중 죽었을 때 락이 영구 점유되지 않도록 하는 안전망 TTL(초).
_LOCK_TTL = 30


def _key(room_id: UUID) -> str:
    return f"chat:response-lock:{room_id}"


class RedisResponseCoordinator:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def try_acquire(
        self, room_id: UUID, source: ChatSource
    ) -> ResponseSlot | None:
        key = _key(room_id)
        token = uuid4().hex
        new_priority = priority_of(source)

        async with self._redis.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current = await pipe.get(key)
                    if current is not None:
                        held_priority = int(current.split(":", 1)[0])
                        if new_priority <= held_priority:
                            await pipe.unwatch()
                            return None
                    pipe.multi()
                    pipe.set(
                        key,
                        f"{new_priority}:{token}:{source.value}",
                        ex=_LOCK_TTL,
                    )
                    await pipe.execute()
                    return ResponseSlot(source=source, token=token)
                except WatchError:
                    continue  # 경합 발생 — 다시 읽고 판단

    async def is_busy(self, room_id: UUID) -> bool:
        # 단순 존재 확인. 잡지 않으므로 WATCH 도 토큰 비교도 필요 없다.
        return await self._redis.exists(_key(room_id)) == 1

    async def still_holds(self, room_id: UUID, slot: ResponseSlot) -> bool:
        # 단순 읽기다 — WATCH가 필요 없다. 읽은 직후 선점당할 수는 있지만, 그건 어떤
        # 검사 방식이든 마찬가지고(생성 결과 발행 자체가 원자적이지 않다), 창이
        # 밀리초 단위로 좁아지는 것으로 충분하다.
        current = await self._redis.get(_key(room_id))
        if current is None:
            return False
        return current.split(":", 2)[1] == slot.token

    async def release(self, room_id: UUID, slot: ResponseSlot) -> None:
        key = _key(room_id)
        async with self._redis.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current = await pipe.get(key)
                    if current is None:
                        await pipe.unwatch()
                        return
                    held_token = current.split(":", 2)[1]
                    if held_token != slot.token:
                        await pipe.unwatch()  # 이미 선점당함 — 건드리지 않는다
                        return
                    pipe.multi()
                    pipe.delete(key)
                    await pipe.execute()
                    return
                except WatchError:
                    continue
