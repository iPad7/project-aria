"""community 영속성 테이블.

`persona_id`·`author_id`는 다른 컨텍스트를 가리키지만 **FK를 걸지 않는다** — 컨텍스트
독립을 물리 스키마에서도 유지한다(`docs/architecture.md`). 인덱스만 둔다.

`status`는 문자열로 저장한다. DB enum 타입을 쓰면 값 추가가 마이그레이션을 요구해
경직되기 때문이다. 값의 유효성은 도메인(`StoryStatus`)이 강제한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field

from aria.common.persistence import TimestampMixin, UUIDMixin


class StoryTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "community_story"

    # 단일 컬럼 인덱스를 걸지 않는다 — 아래 복합 인덱스의 선두 컬럼이라 중복이다.
    persona_id: UUID
    author_id: UUID | None = Field(default=None, index=True)
    title: str
    content: str
    is_anonymous: bool = True
    relationship_stage: str | None = None
    nickname: str | None = None
    status: str = Field(default="pending")

    # 실제 두 질의 패턴에 맞춘 복합 인덱스(docs/data-model.md).
    #  - 게시판 목록: persona_id 필터 + created_at 최신순
    #  - idle 낭독 픽업(B-3): persona_id + status='pending'
    # 모델에 두어야 autogenerate가 이후에도 이 상태를 유지한다.
    __table_args__ = (
        Index(
            "ix_community_story_persona_created",
            "persona_id",
            text("created_at DESC"),
        ),
        Index("ix_community_story_persona_status", "persona_id", "status"),
    )


class LikeTable(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "community_like"

    persona_id: UUID
    user_id: UUID

    # 한 사람이 한 페르소나에 하나만. 멱등 add가 이 제약에 기대므로 DB가 강제해야 한다
    # (동시 요청 두 개가 같이 통과하는 것을 앱 레벨 검사로는 막을 수 없다).
    # 좋아요 수 COUNT도 이 인덱스를 탄다 — persona_id가 선두 컬럼이라서.
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "user_id", name="uq_community_like_persona_user"
        ),
    )
