"""ProcessedRegistry의 Redis 구현 — `SET NX`로 한 번만 처리되게 한다.

`SET key value NX EX ttl`은 "없을 때만 쓰기"를 한 왕복에 한다. 읽고 판단하고 쓰는
방식이면 워커 둘이 같은 메시지를 동시에 받았을 때 둘 다 통과한다 — 조건부 UPDATE로
잔액을 차감하는 것과 같은 이유다(`wallet`의 `_debit`).

TTL은 **재전달 창보다 길어야** 한다. 짧으면 claim이 만료된 뒤 도착한 재전달이 중복을
그대로 통과시킨다. 반대로 너무 길면 claim을 놓지 못한 채 죽은 메시지가 오래 막힌다 —
기본 1시간은 Kafka 재전달(리밸런스 주기)보다 넉넉히 길고, 사람이 DLQ를 들여다보는
주기보다는 짧다.
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis


def _key(msg_id: UUID) -> str:
    return f"chat:processed:{msg_id}"


class RedisProcessedRegistry:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def claim(self, msg_id: UUID) -> bool:
        # nx=True → 이미 있으면 아무 것도 쓰지 않고 None을 돌려준다.
        return bool(await self._redis.set(_key(msg_id), "1", nx=True, ex=self._ttl))

    async def release(self, msg_id: UUID) -> None:
        await self._redis.delete(_key(msg_id))
