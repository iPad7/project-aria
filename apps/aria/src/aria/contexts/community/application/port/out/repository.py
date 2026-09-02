"""아웃바운드 포트: 사연 영속성."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.community.domain.model import Story


class LikeRepository(Protocol):
    """좋아요 영속성.

    add/remove는 **멱등**이다 — 이미 눌렀는데 또 눌러도, 안 눌렀는데 취소해도
    예외 없이 같은 상태로 수렴한다. HTTP PUT/DELETE의 의미와 맞춘 것이다.
    """

    def add(self, persona_id: UUID, user_id: UUID) -> None: ...

    def remove(self, persona_id: UUID, user_id: UUID) -> None: ...

    def exists(self, persona_id: UUID, user_id: UUID) -> bool: ...

    def count_by_persona(self, persona_id: UUID) -> int: ...


class StoryRepository(Protocol):
    def add(self, story: Story) -> None: ...

    def get_by_id(self, story_id: UUID) -> Story | None: ...

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Story]:
        """한 페르소나의 사연을 최신순으로. 게시판 목록용."""
        ...
