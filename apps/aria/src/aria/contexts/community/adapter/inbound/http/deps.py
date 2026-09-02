"""community 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.application.service import StoryService


def get_story_service(
    session: Annotated[Session, Depends(get_session)],
) -> StoryService:
    return StoryService(SqlModelStoryRepository(session))
