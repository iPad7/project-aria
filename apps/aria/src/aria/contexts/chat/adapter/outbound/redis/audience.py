"""RoomAudience의 Redis 구현 — 방 채널의 구독자 수를 그대로 센다.

**따로 기록하지 않는다.** 시청자 명부를 Redis 셋에 두고 heartbeat로 갱신하는 방법도
있지만, 그러면 프로세스가 죽을 때 유령 시청자가 남아 "아무도 없는데 있다고 말하는"
상태가 된다 — 이 단위가 없애려는 바로 그 문제다. WS 연결 하나가 곧 구독 하나이고,
연결이 끊기면 Redis가 구독을 알아서 지우므로 `PUBSUB NUMSUB`이 이미 정확한 답이다.

**한계.** Redis Cluster에서는 `PUBSUB NUMSUB`이 자기 노드의 구독만 센다. 지금은
단일 노드라 맞지만, 샤딩하게 되면 여기가 먼저 깨진다(그때는 shard pub/sub이나
명부 방식으로 바꾼다).
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

from aria.contexts.chat.adapter.outbound.redis.broadcast import room_channel


class RedisRoomAudience:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def viewer_count(self, room_id: UUID) -> int:
        counts = await self._redis.pubsub_numsub(room_channel(room_id))
        # [(채널, 수)] 한 쌍이 돌아온다. 채널을 하나만 물었으므로 첫 쌍의 수가 답이다.
        return int(counts[0][1]) if counts else 0
