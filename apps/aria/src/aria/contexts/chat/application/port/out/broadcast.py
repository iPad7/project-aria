"""방 브로드캐스트 포트 (application/out).

한 방의 이벤트(누군가의 채팅·페르소나 응답)를 그 방에 접속한 모든 연결로 퍼뜨리는
경계. 구현은 프로세스 경계를 넘겨야 하므로(수평 확장) Redis pub/sub이지만, 앱은
"발행하고 구독한다"만 안다.

`subscribe`는 async이며 채널 구독이 성립한 뒤 스트림을 돌려준다 — 구독보다 먼저 발행돼
이벤트가 유실되는 것을 막는다. 스트림은 async 이터레이터라 `aclose()`로 정리한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

RoomEvent = dict[str, Any]


class RoomBroadcaster(Protocol):
    async def publish(self, room_id: UUID, event: RoomEvent) -> None: ...

    async def subscribe(self, room_id: UUID) -> AsyncIterator[RoomEvent]: ...
