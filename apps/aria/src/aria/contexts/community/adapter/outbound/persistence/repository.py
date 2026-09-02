"""StoryRepository 포트의 SQLModel 구현."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, col, select

from aria.contexts.community.adapter.outbound.persistence.model import StoryTable
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
