"""아웃바운드 포트: 사연 영속성."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.community.domain.model import Story


class StoryRepository(Protocol):
    def add(self, story: Story) -> None: ...

    def get_by_id(self, story_id: UUID) -> Story | None: ...

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Story]:
        """한 페르소나의 사연을 최신순으로. 게시판 목록용."""
        ...
