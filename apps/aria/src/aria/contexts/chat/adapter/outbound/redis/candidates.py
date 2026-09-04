"""CandidateBuffer의 Redis 구현 — 방별 리스트.

`RPUSH` + `LTRIM` 으로 최근 N개만 유지하고, 매번 TTL을 다시 건다. 끝난 방송의 잔재가
남지 않도록 하는 유일한 장치다 — 방 종료 훅에 기대지 않는 이유는 워커가 죽거나
방송이 비정상 종료돼도 스스로 사라져야 하기 때문이다.

`take_all`은 **`LPOP key count`** 한 번이다. 읽기와 비우기가 한 명령이라 그 사이에
들어온 후보가 유실되지 않는다 — 처음에는 `LRANGE` + `DELETE` 를 파이프라인으로 묶고
"유실 허용"이라 적었지만, 한 명령으로 되는 것을 굳이 유실시킬 이유가 없었다.

상한과 같은 개수를 뽑으므로 남는 것도 없다.
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
        # 상한만큼 한 번에 뽑는다. 버퍼가 그 이상 자라지 않으므로 남는 것이 없다.
        raw = await self._redis.lpop(_key(room_id), _MAX_CANDIDATES) or []

        candidates: list[Candidate] = []
        for item in raw:
            try:
                candidates.append(Candidate.model_validate_json(item))
            except ValueError:
                # 형식이 바뀐 잔재. 버린다 — 후보 하나 때문에 진행을 멈출 이유가 없다.
                continue
        return candidates
