"""community 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis import Redis as SyncRedis
from sqlmodel import Session

from aria.common.db import get_session
from aria.common.redis import get_sync_redis
from aria.contexts.community.adapter.outbound.cache.like_count import (
    CachedLikeRepository,
)
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelLikeRepository,
    SqlModelStoryRepository,
)
from aria.contexts.community.application.service import LikeService, StoryService


def get_story_service(
    session: Annotated[Session, Depends(get_session)],
) -> StoryService:
    return StoryService(SqlModelStoryRepository(session))


def get_like_service(
    session: Annotated[Session, Depends(get_session)],
    redis: Annotated[SyncRedis, Depends(get_sync_redis)],
) -> LikeService:
    # 캐시 데코레이터로 감싼다 — 서비스는 캐시의 존재를 모른다.
    return LikeService(CachedLikeRepository(SqlModelLikeRepository(session), redis))
