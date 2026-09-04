"""페르소나 프로필 캐시 데코레이터.

프로필은 **거의 안 바뀌는데 생성마다 읽힌다** — 방송 중이면 몇 초에 한 번씩이다.
읽기와 쓰기의 비대칭이 캐시의 교과서적 조건이다.

**열혈순위 캐시와 다른 점: 여기는 무효화 훅을 걸 수 있다.** 랭킹은 쓰기(후원)가
다른 컨텍스트의 트랜잭션 안에서 일어나 훅을 걸 곳이 없었지만, 프로필은 **쓰기도
persona의 것**이다. 관리자가 말투를 바꾸면 즉시 반영된다.

무효화가 api 프로세스에서 일어나고 읽기는 워커 프로세스에서 일어나지만, Redis가
공유라 문제없다. TTL은 무효화를 놓쳤을 때의 안전망이다.

읽기 경로가 async라 `redis.asyncio`를 쓰고, 무효화는 sync 핸들러(persona HTTP)에서
불리므로 sync 클라이언트를 쓴다 — 같은 키를 양쪽에서 다룬다.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from redis import Redis as SyncRedis
from redis import RedisError
from redis.asyncio import Redis

from aria.common.persona_profile import PersonaProfile

logger = logging.getLogger(__name__)

# 무효화를 놓쳤을 때의 안전망. 프로필이 거의 안 바뀌므로 길게 잡는다.
_TTL_SECONDS = 300


def _key(persona_id: UUID) -> str:
    return f"persona:profile:{persona_id}"


def _to_json(profile: PersonaProfile) -> str:
    return json.dumps(
        {
            "persona_id": str(profile.persona_id),
            "name": profile.name,
            "description": profile.description,
            "tone": profile.tone,
            "sentence_length": profile.sentence_length,
            "question_style": profile.question_style,
            "directness": profile.directness,
            "empathy_expression": profile.empathy_expression,
            "core_values": list(profile.core_values),
        }
    )


def _from_json(raw: str) -> PersonaProfile:
    data = json.loads(raw)
    return PersonaProfile(
        persona_id=UUID(data["persona_id"]),
        name=data["name"],
        description=data["description"],
        tone=data["tone"],
        sentence_length=data["sentence_length"],
        question_style=data["question_style"],
        directness=data["directness"],
        empathy_expression=data["empathy_expression"],
        core_values=tuple(data["core_values"]),
    )


class CachedPersonaProfiles:
    """`PersonaProfilePort`를 구현하면서 `PersonaProfilePort`를 감싼다."""

    def __init__(self, inner: object, redis: Redis) -> None:
        self._inner = inner
        self._redis = redis

    async def profile_of(self, persona_id: UUID) -> PersonaProfile | None:
        cached = await self._get(persona_id)
        if cached is not None:
            return cached
        profile = await self._inner.profile_of(persona_id)  # type: ignore[attr-defined]
        if profile is not None:
            # 없는 페르소나(None)는 캐시하지 않는다 — 잘못된 id로 캐시를 채우는
            # 공격이 가능해지고, 새로 만든 페르소나가 TTL만큼 안 보이게 된다.
            await self._set(profile)
        return profile

    async def _get(self, persona_id: UUID) -> PersonaProfile | None:
        try:
            raw = await self._redis.get(_key(persona_id))
        except RedisError:
            logger.warning("프로필 캐시 조회 실패 — DB로 폴백", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return _from_json(raw)
        except (ValueError, TypeError, KeyError):
            # 손상됐거나 형식이 바뀐 값. 지우고 DB에서 다시 읽는다.
            try:
                await self._redis.delete(_key(persona_id))
            except RedisError:
                pass
            return None

    async def _set(self, profile: PersonaProfile) -> None:
        try:
            await self._redis.set(
                _key(profile.persona_id), _to_json(profile), ex=_TTL_SECONDS
            )
        except RedisError:
            logger.warning("프로필 캐시 기록 실패 — 무시", exc_info=True)


def invalidate(redis: SyncRedis, persona_id: UUID) -> None:
    """프로필이 바뀌었을 때 persona의 쓰기 경로가 부른다.

    지우지 못하면 최대 TTL만큼 옛 말투로 말한다. 쓰기를 되돌릴 이유는 없다.
    """
    try:
        redis.delete(_key(persona_id))
    except RedisError:
        logger.warning("프로필 캐시 무효화 실패 — TTL로 수렴", exc_info=True)
