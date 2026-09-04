"""IdleLock의 Redis 구현 — `SET NX`.

`RedisProcessedRegistry`와 같은 형태다. "없을 때만 쓰기"를 한 왕복에 하므로 워커
둘이 동시에 같은 방을 집어도 하나만 통과한다.

**TTL은 한 번의 idle 진행보다 길고, 틱 간격보다도 길어야 한다.** 짧으면 아직
생성 중인 방을 다른 워커가 또 집는다. 길면 워커가 죽었을 때 그 방이 그만큼 조용하다.
LLM 호출 한 번을 넉넉히 덮는 30초로 둔다 — `ResponseCoordinator`의 락 TTL과 같은 값이고,
같은 것(한 번의 생성)을 덮기 때문이다.
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

# 한 번의 idle 진행(사연 claim + 생성 요청)을 덮는 TTL(초).
_LOCK_TTL = 30


def _key(room_id: UUID) -> str:
    return f"chat:idle-lock:{room_id}"


class RedisIdleLock:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(self, room_id: UUID) -> bool:
        return bool(await self._redis.set(_key(room_id), "1", nx=True, ex=_LOCK_TTL))

    async def release(self, room_id: UUID) -> None:
        await self._redis.delete(_key(room_id))
