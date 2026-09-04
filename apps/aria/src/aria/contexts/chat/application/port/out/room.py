"""아웃바운드 포트: 방 영속성.

**이 포트는 async다.** chat은 WS·생성 조율 때문에 위아래가 전부 async인데, SQLModel
Session은 sync다. sync 리포지토리를 async 핸들러에서 그대로 부르면 이벤트 루프가
멈추므로, 어댑터가 `anyio.to_thread`로 넘긴다 — `CommunityStoryFeed`·`WalletSuperchat`가
같은 이유로 쓰는 방식이다. community·wallet의 리포지토리가 sync인 것은 그쪽 HTTP
핸들러가 sync 함수라 FastAPI가 이미 스레드풀에서 돌려 주기 때문이고, 여기는 아니다.

`chat`이 처음으로 갖는 영속 계층이다. 그전까지는 Redis만 썼다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.chat.domain.room import Room, RoomStatus


class RoomRepository(Protocol):
    async def add(self, room: Room) -> None:
        """방을 저장한다.

        같은 페르소나의 live 방이 이미 있으면 `ConflictError`. 이 검사는 DB의 부분
        유일 인덱스가 한다 — 앱에서 "이미 있나?"를 먼저 보는 방식으로는 동시 요청
        둘을 막을 수 없다(community의 좋아요, wallet의 멱등키와 같은 이유).
        """
        ...

    async def get_by_id(self, room_id: UUID) -> Room | None: ...

    async def save(self, room: Room) -> None:
        """상태 전이를 반영한다. live 유일성은 `add`와 같은 인덱스가 지킨다."""
        ...

    async def list_by_status(
        self, status: RoomStatus, *, limit: int, offset: int
    ) -> list[Room]:
        """상태로 거른 목록. 최신순."""
        ...
