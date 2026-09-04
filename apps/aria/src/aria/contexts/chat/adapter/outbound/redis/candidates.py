"""CandidateBuffer의 Redis 구현 — 방별 리스트.

`RPUSH` + `LTRIM` 으로 최근 N개만 유지하고, 매번 TTL을 다시 건다. 끝난 방송의 잔재가
남지 않도록 하는 유일한 장치다 — 방 종료 훅에 기대지 않는 이유는 워커가 죽거나
방송이 비정상 종료돼도 스스로 사라져야 하기 때문이다.

`take_all`은 `LRANGE` + `DELETE`를 파이프라인 한 번에 묶는다. 둘 사이에 새 후보가
들어오면 그건 유실이지만, 채팅 후보는 유실 허용 데이터다(pub/sub 팬아웃과 같은 성격).
원자성을 위해 Lua를 들이는 것은 이 데이터의 값어치에 비해 과하다.
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

from aria.contexts.chat.domain.topic import Candidate

# 한 방에 쌓아 둘 최대 후보 수. 넘으면 오래된 것부터 밀려난다.
_MAX_CANDIDATES = 50
# 이보다 오래 조용한 방의 후보는 사라진다. 어차피 답할 가치가 없는 나이다.
_TTL_SECONDS = 300


def _key(room_id: UUID) -> str:
    return f"chat:candidates:{room_id}"


class RedisCandidateBuffer:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, room_id: UUID, candidate: Candidate) -> None:
        key = _key(room_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, candidate.model_dump_json())
            pipe.ltrim(key, -_MAX_CANDIDATES, -1)
            pipe.expire(key, _TTL_SECONDS)
            await pipe.execute()

    async def take_all(self, room_id: UUID) -> list[Candidate]:
        key = _key(room_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            raw, _ = await pipe.execute()

        candidates: list[Candidate] = []
        for item in raw:
            try:
                candidates.append(Candidate.model_validate_json(item))
            except ValueError:
                # 형식이 바뀐 잔재. 버린다 — 후보 하나 때문에 진행을 멈출 이유가 없다.
                continue
        return candidates
