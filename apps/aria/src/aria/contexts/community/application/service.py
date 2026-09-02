"""community 유스케이스 — 사연 게시판.

persona 슬라이스와 달리 소유권 검사가 없다. 사연은 **누구나 쓰고 누구나 읽는**
공개 게시판이기 때문이다(FR-STATION-2/3). 작성자는 인증 주체에서 오고, 표시 여부는
`is_anonymous`가 결정한다.
"""

from __future__ import annotations

from uuid import UUID

from aria.common.errors import NotFoundError
from aria.contexts.community.application.port.out.repository import (
    LikeRepository,
    StoryRepository,
)
from aria.contexts.community.domain.model import Story

# 게시판 한 페이지 크기의 상한. 무한정 긁어가는 것을 막는다.
MAX_PAGE_SIZE = 100


class StoryService:
    def __init__(self, stories: StoryRepository) -> None:
        self._stories = stories

    def submit(
        self,
        persona_id: UUID,
        author_id: UUID,
        title: str,
        content: str,
        *,
        is_anonymous: bool = True,
        relationship_stage: str | None = None,
        nickname: str | None = None,
    ) -> Story:
        story = Story(
            persona_id=persona_id,
            author_id=author_id,
            title=title,
            content=content,
            is_anonymous=is_anonymous,
            relationship_stage=relationship_stage,
            nickname=nickname,
        )
        self._stories.add(story)
        return story

    def list_for_persona(
        self, persona_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[Story]:
        return self._stories.list_by_persona(
            persona_id, limit=min(limit, MAX_PAGE_SIZE), offset=max(offset, 0)
        )

    def get(self, story_id: UUID) -> Story:
        story = self._stories.get_by_id(story_id)
        if story is None:
            raise NotFoundError("사연을 찾을 수 없습니다", code="story_not_found")
        return story


class LikeService:
    """좋아요 유스케이스.

    좋아요/취소는 멱등이라 "이미 눌렀음" 같은 예외를 만들지 않는다. 클라이언트가
    재시도해도 안전해야 하기 때문이다(FR-STATION-5의 '토글'은 UI의 표현이고,
    API는 원하는 최종 상태를 선언하는 형태로 둔다).
    """

    def __init__(self, likes: LikeRepository) -> None:
        self._likes = likes

    def like(self, persona_id: UUID, user_id: UUID) -> None:
        self._likes.add(persona_id, user_id)

    def unlike(self, persona_id: UUID, user_id: UUID) -> None:
        self._likes.remove(persona_id, user_id)

    def liked_by(self, persona_id: UUID, user_id: UUID) -> bool:
        return self._likes.exists(persona_id, user_id)

    def count(self, persona_id: UUID) -> int:
        return self._likes.count_by_persona(persona_id)
