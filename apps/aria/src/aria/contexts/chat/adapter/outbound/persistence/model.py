"""chat 테이블 정의.

`persona_id`·`host_id`는 다른 컨텍스트를 가리키지만 **cross-context FK를 걸지 않는다**
(인덱스만) — 컨텍스트 독립을 물리 스키마까지 관철하기 위해서다(`docs/architecture.md`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Index, text
from sqlmodel import Field

from aria.common.persistence import TimestampMixin, UUIDMixin


class RoomTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "chat_room"

    persona_id: UUID = Field(index=True)
    host_id: UUID = Field(index=True)
    name: str = Field(max_length=255)
    description: str | None = None
    thumbnail_url: str | None = Field(default=None, max_length=512)
    status: str = Field(default="pending", index=True)

    __table_args__ = (
        # 한 페르소나는 동시에 하나의 live 방만 가진다. 스트리머가 두 방송을 동시에
        # 할 수는 없다.
        #
        # **부분 유일 인덱스**여야 한다. 그냥 unique(persona_id)로 걸면 그 페르소나가
        # 두 번째 방송을 영영 못 연다(끝난 방도 행으로 남으므로). `WHERE status='live'`가
        # 살아 있는 방에만 유일성을 건다.
        #
        # 앱에서 "이미 live가 있나?"를 먼저 보는 방식으로는 동시 요청 둘이 같은 답을
        # 보고 둘 다 통과한다 — community의 좋아요, wallet의 멱등키와 같은 이유로
        # 제약을 DB에 둔다. 방언마다 키워드가 달라 둘 다 준다(테스트는 SQLite).
        Index(
            "uq_chat_room_live_persona",
            "persona_id",
            unique=True,
            postgresql_where=text("status = 'live'"),
            sqlite_where=text("status = 'live'"),
        ),
        # 라이브 목록이 타는 인덱스.
        Index("ix_chat_room_status_created", "status", text("created_at DESC")),
    )
