"""RoomRepository의 SQLModel 구현.

**포트가 async인데 Session은 sync다.** 각 메서드가 `anyio.to_thread`로 블로킹 호출을
넘긴다 — chat은 위아래가 async라 여기서 그냥 부르면 이벤트 루프가 멈춘다.
`CommunityStoryFeed`·`WalletSuperchat`가 쓰는 방식과 같다. 세션이 스레드를 넘나드는
것처럼 보이지만, 한 호출이 끝날 때까지 그 스레드만 세션을 쓰므로 동시 접근은 없다.
"""

from __future__ import annotations

from uuid import UUID

import anyio.to_thread
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from aria.common.errors import ConflictError
from aria.contexts.chat.adapter.outbound.persistence.model import RoomTable
from aria.contexts.chat.domain.room import Room, RoomStatus


def _to_domain(row: RoomTable) -> Room:
    return Room(
        id=row.id,
        persona_id=row.persona_id,
        host_id=row.host_id,
        name=row.name,
        description=row.description,
        thumbnail_url=row.thumbnail_url,
        status=RoomStatus(row.status),
    )


class SqlModelRoomRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def add(self, room: Room) -> None:
        await anyio.to_thread.run_sync(self._add, room)

    async def get_by_id(self, room_id: UUID) -> Room | None:
        return await anyio.to_thread.run_sync(self._get_by_id, room_id)

    async def save(self, room: Room) -> None:
        await anyio.to_thread.run_sync(self._save, room)

    async def list_by_status(
        self, status: RoomStatus, *, limit: int, offset: int
    ) -> list[Room]:
        def _query() -> list[Room]:
            rows = self._session.exec(
                select(RoomTable)
                .where(RoomTable.status == status.value)
                .order_by(col(RoomTable.created_at).desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_to_domain(row) for row in rows]

        return await anyio.to_thread.run_sync(_query)

    # --- 스레드에서 도는 sync 본체 ------------------------------------------

    def _add(self, room: Room) -> None:
        self._session.add(
            RoomTable(
                id=room.id,
                persona_id=room.persona_id,
                host_id=room.host_id,
                name=room.name,
                description=room.description,
                thumbnail_url=room.thumbnail_url,
                status=room.status.value,
            )
        )
        self._commit_guarding_live_uniqueness()

    def _get_by_id(self, room_id: UUID) -> Room | None:
        row = self._session.get(RoomTable, room_id)
        return _to_domain(row) if row is not None else None

    def _save(self, room: Room) -> None:
        row = self._session.get(RoomTable, room.id)
        if row is None:
            return
        row.status = room.status.value
        row.name = room.name
        row.description = room.description
        row.thumbnail_url = room.thumbnail_url
        self._session.add(row)
        self._commit_guarding_live_uniqueness()

    def _commit_guarding_live_uniqueness(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            # 부분 유일 인덱스에 걸렸다 = 그 페르소나가 이미 방송 중이다.
            self._session.rollback()
            raise ConflictError(
                "이미 방송 중인 방이 있습니다", code="already_live"
            ) from exc
