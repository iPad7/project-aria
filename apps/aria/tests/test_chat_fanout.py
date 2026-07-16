"""RoomBroadcaster가 프로세스 경계를 넘는지 검증.

FakeServer 하나를 공유하는 FakeAsyncRedis 두 개 = 같은 Redis에 붙은 서로 다른 두
'프로세스'. 한 클라이언트가 발행한 이벤트를 다른 클라이언트의 구독이 받으면, 인메모리
dict였다면 갈라졌을 방 상태가 Redis pub/sub으로 공유됨을 뜻한다(수평 확장 성립).
"""

import asyncio
from uuid import uuid4

from fakeredis import FakeAsyncRedis, FakeServer

from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster


async def test_publish_crosses_processes() -> None:
    server = FakeServer()
    process_a = FakeAsyncRedis(server=server, decode_responses=True)
    process_b = FakeAsyncRedis(server=server, decode_responses=True)
    room = uuid4()

    # 프로세스 B가 구독을 성립시킨다.
    stream = await RedisRoomBroadcaster(process_b).subscribe(room)

    # 프로세스 A가 발행한다.
    await RedisRoomBroadcaster(process_a).publish(
        room, {"type": "message", "text": "A에서 보냄"}
    )

    event = await asyncio.wait_for(anext(stream), timeout=2)
    assert event["text"] == "A에서 보냄"
    await stream.aclose()


async def test_subscribe_isolates_rooms() -> None:
    server = FakeServer()
    pub = FakeAsyncRedis(server=server, decode_responses=True)
    sub = FakeAsyncRedis(server=server, decode_responses=True)
    room, other_room = uuid4(), uuid4()

    stream = await RedisRoomBroadcaster(sub).subscribe(room)

    # 다른 방에 발행한 것은 이 구독에 오면 안 되고, 같은 방 것만 와야 한다.
    caster = RedisRoomBroadcaster(pub)
    await caster.publish(other_room, {"type": "message", "text": "다른 방"})
    await caster.publish(room, {"type": "message", "text": "내 방"})

    event = await asyncio.wait_for(anext(stream), timeout=2)
    assert event["text"] == "내 방"
    await stream.aclose()
