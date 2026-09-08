"""`TranscriptRepository`의 SQLModel 구현.

`SqlModelRoomRepository`와 같은 방식이다 — 포트가 async고 Session은 sync라 각 메서드가
`anyio.to_thread`로 블로킹 호출을 넘긴다.

**커밋을 여기서 한다.** 기록 한 줄은 그 자체로 완결이라 밖에서 묶을 트랜잭션이 없다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import anyio.to_thread
from sqlmodel import Session, col, select

from aria.contexts.chat.adapter.outbound.persistence.model import MessageTable
from aria.contexts.chat.domain.message import MessageKind, RoomMessage
from aria.contexts.chat.domain.source import ChatSource

# 한 페이지 상한. 다른 컨텍스트와 같은 값 — 페이징 정책을 다르게 둘 이유가 없다.
MAX_PAGE_SIZE = 100


def _aware(value: datetime) -> datetime:
    """DB에서 온 시각을 UTC 기준으로 맞춘다.

    Postgres는 tz를 붙여 주지만 SQLite(테스트)는 naive로 돌려준다 — 그대로 내보내면
    같은 코드가 환경마다 다른 것을 준다(`SqlModelRoomRepository`가 겪은 그 자리다).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_domain(row: MessageTable) -> RoomMessage:
    return RoomMessage(
        id=row.id,
        room_id=row.room_id,
        kind=MessageKind(row.kind),
        text=row.text,
        author_id=row.author_id,
        persona_id=row.persona_id,
        source=ChatSource(row.source) if row.source else None,
        model_version=row.model_version,
        amount=row.amount,
        replied_to=row.replied_to,
        created_at=_aware(row.created_at),
    )


class SqlModelTranscriptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def append(self, message: RoomMessage) -> None:
        await anyio.to_thread.run_sync(self._append, message)

    async def list_recent(
        self, room_id: UUID, *, limit: int = 50, before: UUID | None = None
    ) -> list[RoomMessage]:
        capped = min(max(limit, 1), MAX_PAGE_SIZE)
        return await anyio.to_thread.run_sync(
            self._list_recent, room_id, capped, before
        )

    def _append(self, message: RoomMessage) -> None:
        self._session.add(
            MessageTable(
                id=message.id,
                room_id=message.room_id,
                kind=message.kind.value,
                text=message.text,
                author_id=message.author_id,
                persona_id=message.persona_id,
                source=message.source.value if message.source else None,
                model_version=message.model_version,
                amount=message.amount,
                replied_to=message.replied_to,
            )
        )
        self._session.commit()

    def _list_recent(
        self, room_id: UUID, limit: int, before: UUID | None
    ) -> list[RoomMessage]:
        statement = select(MessageTable).where(MessageTable.room_id == room_id)
        if before is not None:
            # UUIDv7이라 id 비교가 곧 시간 비교다 — 별도 커서 컬럼이 필요 없다.
            statement = statement.where(col(MessageTable.id) < before)
        rows = self._session.exec(
            statement.order_by(col(MessageTable.id).desc()).limit(limit)
        ).all()
        # 워커는 세션을 오래 들고 있으므로 읽은 로우를 놓아 준다 — 다음 조회가
        # 캐시된 옛 값을 집어 오지 않게.
        self._session.expunge_all()
        return [_to_domain(row) for row in rows]
