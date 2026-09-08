"""chat 테이블 정의.

`persona_id`·`host_id`는 다른 컨텍스트를 가리키지만 **cross-context FK를 걸지 않는다**
(인덱스만) — 컨텍스트 독립을 물리 스키마까지 관철하기 위해서다(`docs/architecture.md`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

# `text`는 `MessageTable`의 컬럼 이름이기도 하다 — 클래스 본문에서 그 이름이 가려져
# `text("id DESC")`가 FieldInfo를 부르려다 죽는다. 그래서 별칭으로 들여온다.
from sqlalchemy import DateTime, Index
from sqlalchemy import text as sa_text
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
    # 방송이 끝난 시각. `updated_at`으로 갈음하지 않는다 — 그쪽은 썸네일만 바꿔도
    # 움직이므로 "언제 끝났나"의 답이 되지 못한다.
    closed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

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
            postgresql_where=sa_text("status = 'live'"),
            sqlite_where=sa_text("status = 'live'"),
        ),
        # 라이브 목록이 타는 인덱스.
        Index("ix_chat_room_status_created", "status", sa_text("created_at DESC")),
    )


class MessageTable(UUIDMixin, TimestampMixin, table=True):
    """방 타임라인 한 줄 — 시청자 채팅·후원·페르소나 응답이 같은 표에 산다.

    종류마다 비는 컬럼이 있다(`amount`는 후원에만, `model_version`은 응답에만). 표를
    쪼개는 대신 그렇게 두는 이유는 주요 질문이 **"이 방에서 무슨 일이 있었나"** 하나이고,
    쪼개면 그 질문이 매번 UNION이 되기 때문이다. 어긋난 조합은 도메인이 막는다.
    """

    __tablename__ = "chat_message"

    # 단독 인덱스를 붙이지 않는다 — 아래 복합 인덱스가 `room_id`를 선두로 갖고 있어
    # 같은 질의를 커버한다. 둘 다 두면 쓰기가 가장 잦은 표에 죽은 인덱스가 하나 는다.
    room_id: UUID
    kind: str = Field(max_length=20)
    text: str = Field(max_length=2000)
    author_id: UUID | None = None
    # 페르소나별로 응답을 모으는 질의가 실제로 있다 — 데이터셋은 페르소나 단위로
    # 뽑힌다(멀티-LoRA). `author_id`에 안 거는 것과의 차이가 그것이다.
    persona_id: UUID | None = Field(default=None, index=True)
    source: str | None = Field(default=None, max_length=20)
    model_version: str | None = Field(default=None, max_length=100)
    amount: int | None = None
    # 이 응답이 답한 시청자 메시지. **FK를 걸지 않는다** — 같은 표를 가리키는 자기참조
    # FK는 보존 정책으로 옛 메시지를 지울 때 응답까지 막거나 끌고 간다. 학습 쌍의
    # 연결선이지 참조 무결성이 필요한 관계가 아니다.
    replied_to: UUID | None = None

    __table_args__ = (
        # 히스토리 조회가 타는 유일한 인덱스: 한 방을 최신순으로. PK가 UUIDv7이라
        # id 내림차순이 곧 시간 내림차순이고, 커서 페이징(`before`)도 이 인덱스를 탄다.
        Index("ix_chat_message_room_id_desc", "room_id", sa_text("id DESC")),
    )
