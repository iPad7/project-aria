"""community inbound DI — 전송 중립.

HTTP가 아닌 경로로 들어오는 요청의 조립을 둔다. `StoryFeedPort` 구현이 그렇다 —
chat이 포트로 호출하는 것이라 HTTP 라우터와 무관하고, 실제 배선은 합성 루트가 한다.
(chat이 같은 이유로 `adapter/inbound/deps.py`를 전송별 폴더 밖에 둔다.)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.adapter.outbound.story_feed import CommunityStoryFeed


def get_story_feed(
    session: Annotated[Session, Depends(get_session)],
) -> CommunityStoryFeed:
    return CommunityStoryFeed(SqlModelStoryRepository(session))
