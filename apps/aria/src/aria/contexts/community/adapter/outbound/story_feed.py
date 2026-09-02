"""StoryFeedPort의 community 구현.

`common.story_feed`의 계약을 community가 채운다. chat은 이 클래스를 모르고,
community도 chat을 모른다 — 양쪽 다 common의 계약만 안다. 배선은 합성 루트가 한다.

**async 포트를 sync 영속 계층으로 구현한다.** chat의 포트는 모두 async인데
(`ActivityTracker`·`PersonaLLMPort`) community의 리포지토리는 sync(SQLModel Session)다.
블로킹 DB 호출을 이벤트 루프에서 그대로 하면 루프가 멈추므로 `anyio.to_thread`로
넘긴다 — async DB 스택을 새로 들이지 않고 경계만 맞추는 방법이다.

세션이 스레드를 넘나드는 것처럼 보이지만, 한 호출이 끝날 때까지 그 스레드만
세션을 쓰므로 동시 접근은 없다.
"""

from __future__ import annotations

from uuid import UUID

import anyio.to_thread

from aria.common.story_feed import PendingStory
from aria.contexts.community.application.port.out.repository import StoryRepository
from aria.contexts.community.domain.model import Story


def _to_pending(story: Story) -> PendingStory:
    return PendingStory(
        story_id=story.id,
        persona_id=story.persona_id,
        title=story.title,
        content=story.content,
        nickname=story.nickname,
    )


class CommunityStoryFeed:
    def __init__(self, stories: StoryRepository) -> None:
        self._stories = stories

    async def claim_next_pending(self, persona_id: UUID) -> PendingStory | None:
        story = await anyio.to_thread.run_sync(
            self._stories.claim_next_pending, persona_id
        )
        return _to_pending(story) if story is not None else None

    async def mark_done(self, story_id: UUID) -> None:
        await anyio.to_thread.run_sync(self._stories.mark_done, story_id)
