"""RoomBroadcaster의 Redis pub/sub 구현.

방마다 채널 `chat:room:{room_id}`. `publish`는 이벤트를 JSON으로 채널에 발행하고,
`subscribe`는 채널 구독을 성립시킨 뒤(유실 방지) 발행분을 흘리는 async 스트림을 준다.
프로세스마다 각자 Redis에 구독하므로, 한 프로세스가 발행하면 방을 구독한 모든
프로세스가 받는다 — 수평 확장에서 방 상태가 갈라지지 않는 이유.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

from redis.asyncio import Redis

from aria.contexts.chat.application.port.out.broadcast import RoomEvent


def _channel(room_id: UUID) -> str:
    return f"chat:room:{room_id}"


async def _stream(pubsub: object, channel: str) -> AsyncIterator[RoomEvent]:
    try:
        async for message in pubsub.listen():  # type: ignore[attr-defined]
            if message["type"] == "message":
                yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(channel)  # type: ignore[attr-defined]
        await pubsub.aclose()  # type: ignore[attr-defined]


class RedisRoomBroadcaster:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, room_id: UUID, event: RoomEvent) -> None:
        await self._redis.publish(_channel(room_id), json.dumps(event))

    async def subscribe(self, room_id: UUID) -> AsyncIterator[RoomEvent]:
        channel = _channel(room_id)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)  # 구독 성립 후 반환 → 이후 발행분은 유실 없음
        return _stream(pubsub, channel)
