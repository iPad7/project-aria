"""community 리포지토리 포트의 SQLModel 구현 (Story · Like)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from aria.contexts.community.adapter.outbound.persistence.model import (
    LikeTable,
    StoryTable,
)
from aria.contexts.community.domain.model import Story, StoryStatus


def _to_domain(row: StoryTable) -> Story:
    return Story(
        id=row.id,
        persona_id=row.persona_id,
        author_id=row.author_id,
        title=row.title,
        content=row.content,
        is_anonymous=row.is_anonymous,
        relationship_stage=row.relationship_stage,
        nickname=row.nickname,
        status=StoryStatus(row.status),
    )


class SqlModelStoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, story: Story) -> None:
        self._session.add(
            StoryTable(
                id=story.id,
                persona_id=story.persona_id,
                author_id=story.author_id,
                title=story.title,
                content=story.content,
                is_anonymous=story.is_anonymous,
                relationship_stage=story.relationship_stage,
                nickname=story.nickname,
                status=story.status.value,
            )
        )
        self._session.commit()

    def get_by_id(self, story_id: UUID) -> Story | None:
        row = self._session.get(StoryTable, story_id)
        return _to_domain(row) if row is not None else None

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Story]:
        # 최신순. id가 UUIDv7이라 시간순으로 정렬되지만, 정렬 의도를 드러내려고
        # created_at을 명시한다(id 생성 전략이 바뀌어도 동작이 유지된다).
        rows = self._session.exec(
            select(StoryTable)
            .where(StoryTable.persona_id == persona_id)
            .order_by(col(StoryTable.created_at).desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [_to_domain(row) for row in rows]


class SqlModelLikeRepository:
    """LikeRepository의 SQLModel 구현.

    add/remove가 멱등이다 — 이미 있으면 조용히 넘어가고, 없으면 조용히 넘어간다.
    유일 제약이 동시 요청까지 막아주므로, 경합으로 제약 위반이 나면 그것도 "이미
    눌린 상태"라는 뜻이라 성공으로 흡수한다.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, persona_id: UUID, user_id: UUID) -> None:
        if self.exists(persona_id, user_id):
            return
        self._session.add(LikeTable(persona_id=persona_id, user_id=user_id))
        try:
            self._session.commit()
        except IntegrityError:
            # 동시에 같은 좋아요가 두 번 들어온 경우. 원하는 최종 상태는 이미 달성됐다.
            self._session.rollback()

    def remove(self, persona_id: UUID, user_id: UUID) -> None:
        row = self._session.exec(
            select(LikeTable).where(
                LikeTable.persona_id == persona_id, LikeTable.user_id == user_id
            )
        ).first()
        if row is None:
            return
        self._session.delete(row)
        self._session.commit()

    def exists(self, persona_id: UUID, user_id: UUID) -> bool:
        row = self._session.exec(
            select(LikeTable.id).where(
                LikeTable.persona_id == persona_id, LikeTable.user_id == user_id
            )
        ).first()
        return row is not None

    def count_by_persona(self, persona_id: UUID) -> int:
        return int(
            self._session.exec(
                select(func.count())
                .select_from(LikeTable)
                .where(LikeTable.persona_id == persona_id)
            ).one()
        )
