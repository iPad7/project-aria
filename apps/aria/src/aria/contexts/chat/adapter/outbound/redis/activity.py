"""ActivityTracker의 Redis 구현.

방마다 마지막 활동 시각(epoch 초)을 키 하나에 담는다. 프로세스 로컬이 아니라
Redis에 있으므로 어느 인스턴스가 처리하든 같은 idle 판단을 본다.
"""

from __future__ import annotations

import time
from uuid import UUID

from redis.asyncio import Redis

# 활동 없는 방의 키가 영원히 남지 않도록 넉넉한 TTL(초). 만료되면 곧 idle로 간주된다.
_ACTIVITY_TTL = 3600


def _key(room_id: UUID) -> str:
    return f"chat:activity:{room_id}"


class RedisActivityTracker:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def touch(self, room_id: UUID) -> None:
        await self._redis.set(_key(room_id), repr(time.time()), ex=_ACTIVITY_TTL)

    async def is_idle(self, room_id: UUID, threshold_seconds: float) -> bool:
        elapsed = await self.seconds_since_last(room_id)
        return elapsed is None or elapsed >= threshold_seconds

    async def seconds_since_last(self, room_id: UUID) -> float | None:
        value = await self._redis.get(_key(room_id))
        if value is None:
            return None
        return time.time() - float(value)
