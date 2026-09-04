"""방 유스케이스 — 개설·상태 전이·조회.

**개설 권한은 여기서 보지 않는다.** chat은 persona를 import할 수 없어 "이 페르소나가
당신 것인가"를 확인할 수 없다. 커널 포트를 하나 더 만드는 대신 PRD FR-AUTH-3
("관리자는 방송·페르소나·TTS 설정을 관리한다")을 근거로 **staff 전용**으로 좁혔고,
그 검사는 HTTP 경계의 `require_staff`가 한다. 일반 사용자 호스트를 열게 되면 그때
`PersonaOwnershipPort`가 필요해진다 — 요구사항이 바뀌는 시점이다.

`ensure_open` 은 채팅·후원 경로가 부른다. 그전까지 `room_id`는 아무 UUID나 됐고,
그래서 **존재하지 않는 방에 크레딧을 태울 수** 있었다.
"""

from __future__ import annotations

from uuid import UUID

from aria.common.errors import NotFoundError
from aria.contexts.chat.application.port.out.room import RoomRepository
from aria.contexts.chat.domain.room import Room, RoomStatus

# 목록 한 페이지의 상한.
MAX_PAGE_SIZE = 100


class RoomClosedError(NotFoundError):
    """라이브가 아닌 방에 말을 걸었다.

    `NotFoundError`를 물려받는다 — 방이 없는 것과 아직/이미 라이브가 아닌 것은
    시청자 입장에서 같은 일이고, 상태를 구분해 알려 주면 개설만 해 둔 방송의 존재가
    밖으로 샌다.
    """

    code = "room_not_live"


class RoomService:
    def __init__(self, rooms: RoomRepository) -> None:
        self._rooms = rooms

    async def open(
        self,
        persona_id: UUID,
        host_id: UUID,
        name: str,
        *,
        description: str | None = None,
        thumbnail_url: str | None = None,
    ) -> Room:
        room = Room(
            persona_id=persona_id,
            host_id=host_id,
            name=name,
            description=description,
            thumbnail_url=thumbnail_url,
        )
        await self._rooms.add(room)
        return room

    async def get(self, room_id: UUID) -> Room:
        room = await self._rooms.get_by_id(room_id)
        if room is None:
            raise NotFoundError("방을 찾을 수 없습니다", code="room_not_found")
        return room

    async def transition(self, room_id: UUID, status: RoomStatus) -> Room:
        room = await self.get(room_id)
        # 규칙은 도메인이 안다 — 허용되지 않으면 여기서 예외가 올라온다.
        room.transition_to(status)
        await self._rooms.save(room)
        return room

    async def list_live(self, *, limit: int = 20, offset: int = 0) -> list[Room]:
        return await self._rooms.list_by_status(
            RoomStatus.LIVE, limit=min(limit, MAX_PAGE_SIZE), offset=max(offset, 0)
        )

    async def ensure_open(self, room_id: UUID) -> Room:
        """채팅·후원을 받을 수 있는 방인지 확인하고 돌려준다.

        후원에서 특히 중요하다 — 차감은 진짜로 일어나므로, 존재하지 않는 방을 향한
        요청이 여기서 막히지 않으면 돈만 빠져나간다.
        """
        room = await self.get(room_id)
        if not room.is_open_for_chat():
            raise RoomClosedError("방송 중인 방이 아닙니다")
        return room
