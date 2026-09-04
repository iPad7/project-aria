"""community 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis import Redis as SyncRedis
from sqlmodel import Session

from aria.common.db import get_session
from aria.common.ranking import DonationRankingPort
from aria.common.redis import get_sync_redis
from aria.common.user_directory import UserDirectoryPort
from aria.contexts.community.adapter.outbound.cache.like_count import (
    CachedLikeRepository,
)
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelLikeRepository,
    SqlModelStoryRepository,
)
from aria.contexts.community.application.service import (
    LikeService,
    RankingService,
    StoryService,
)


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


# --- 컨텍스트 간 포트의 자리 -------------------------------------------------
#
# 열혈순위는 community가 소유하지만 데이터는 wallet(금액)과 identity(이름)에 있다.
# community가 그들을 import할 수 없으므로 여기서는 **자리만 선언**하고, 합성 루트가
# `dependency_overrides`로 구현을 꽂는다(chat이 `SuperchatPort`를 받는 방식 그대로).
#
# 기본 구현을 두지 않고 예외를 던지는 이유: 배선을 빠뜨렸을 때 빈 순위표가 조용히
# 나가는 대신 즉시 드러나야 한다.


def get_donation_ranking() -> DonationRankingPort:
    raise NotImplementedError(
        "DonationRankingPort가 배선되지 않았습니다 — aria/app.py의 합성 루트를 확인하세요."
    )


def get_user_directory() -> UserDirectoryPort:
    raise NotImplementedError(
        "UserDirectoryPort가 배선되지 않았습니다 — aria/app.py의 합성 루트를 확인하세요."
    )


def get_ranking_service(
    ranking: Annotated[DonationRankingPort, Depends(get_donation_ranking)],
    directory: Annotated[UserDirectoryPort, Depends(get_user_directory)],
) -> RankingService:
    return RankingService(ranking, directory)
