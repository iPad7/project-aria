"""persona 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis import Redis as SyncRedis
from sqlmodel import Session

from aria.common.db import get_session
from aria.common.redis import get_sync_redis
from aria.contexts.persona.adapter.outbound.cache.profile import invalidate
from aria.contexts.persona.adapter.outbound.persistence.repository import (
    SqlModelPersonaRepository,
    SqlModelProfileRepository,
)
from aria.contexts.persona.application.service import (
    PersonaProfileService,
    PersonaService,
)


def get_persona_service(
    session: Annotated[Session, Depends(get_session)],
) -> PersonaService:
    return PersonaService(SqlModelPersonaRepository(session))


def get_profile_service(
    session: Annotated[Session, Depends(get_session)],
    redis: Annotated[SyncRedis, Depends(get_sync_redis)],
) -> PersonaProfileService:
    # 프로필이 바뀌면 캐시를 지운다 — 읽는 쪽은 워커 프로세스지만 Redis가 공유라
    # 곧바로 반영된다. 열혈순위와 달리 쓰기가 이 컨텍스트의 것이라 훅을 걸 수 있다.
    return PersonaProfileService(
        PersonaService(SqlModelPersonaRepository(session)),
        SqlModelProfileRepository(session),
        on_change=lambda persona_id: invalidate(redis, persona_id),
    )
