"""좋아요 수 캐시 데코레이터.

방송국 페이지는 시청자마다 뜨는 핫 리드 경로다. `COUNT`는 매칭 행 수에 비례하므로
페르소나당 좋아요가 수십만이 되면 조회가 눈에 띄게 느려진다(10만 ≈ 10ms,
100만 ≈ 50~100ms). 그 앞에 캐시를 둔다.

**DB가 진실이고 Redis는 캐시일 뿐이다.** 증감을 Redis에 누적하지 않기 때문에 캐시가
날아가거나 비어 있어도 다음 조회에서 COUNT로 복구된다 — 카운터를 저장하는 방식의
정합성 문제(누락된 감소, 재시작 시 유실)를 아예 만들지 않는다.

좋아요/취소 시 키를 지우므로 **본인 동작은 즉시 반영**되고, 남의 좋아요는 최대 TTL만큼
지연된다. 좋아요 수에는 허용 가능한 오차다.

`LikeRepository`를 구현하면서 `LikeRepository`를 감싼다 — `FallbackPersonaLLM`과 같은
데코레이터 패턴이라 서비스는 캐시의 존재를 모른다.
"""

from __future__ import annotations

import logging
from uuid import UUID

from redis import Redis, RedisError

from aria.contexts.community.application.port.out.repository import LikeRepository

logger = logging.getLogger(__name__)

# 남의 좋아요가 반영되기까지의 최대 지연(초).
_TTL_SECONDS = 60


def _key(persona_id: UUID) -> str:
    return f"community:like_count:{persona_id}"


class CachedLikeRepository:
    def __init__(self, inner: LikeRepository, redis: Redis) -> None:
        self._inner = inner
        self._redis = redis

    def add(self, persona_id: UUID, user_id: UUID) -> None:
        self._inner.add(persona_id, user_id)
        self._invalidate(persona_id)

    def remove(self, persona_id: UUID, user_id: UUID) -> None:
        self._inner.remove(persona_id, user_id)
        self._invalidate(persona_id)

    def exists(self, persona_id: UUID, user_id: UUID) -> bool:
        # 유일 인덱스 조회라 이미 O(1)이다 — 캐시할 이유가 없다.
        return self._inner.exists(persona_id, user_id)

    def count_by_persona(self, persona_id: UUID) -> int:
        cached = self._get(persona_id)
        if cached is not None:
            return cached
        count = self._inner.count_by_persona(persona_id)
        self._set(persona_id, count)
        return count

    # --- Redis 접근. 캐시 장애가 기능을 멈추게 하지 않는다 -----------------------
    #
    # Redis가 죽어도 좋아요는 계속 동작해야 한다. 캐시는 성능 장치이지 정확성의
    # 일부가 아니므로, 실패하면 로그만 남기고 DB 경로로 계속 간다.

    def _get(self, persona_id: UUID) -> int | None:
        try:
            raw = self._redis.get(_key(persona_id))
        except RedisError:
            logger.warning("좋아요 수 캐시 조회 실패 — DB로 폴백", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            # 손상된 값. 지우고 DB에서 다시 읽는다.
            self._invalidate(persona_id)
            return None

    def _set(self, persona_id: UUID, count: int) -> None:
        try:
            self._redis.set(_key(persona_id), count, ex=_TTL_SECONDS)
        except RedisError:
            logger.warning("좋아요 수 캐시 기록 실패 — 무시", exc_info=True)

    def _invalidate(self, persona_id: UUID) -> None:
        try:
            self._redis.delete(_key(persona_id))
        except RedisError:
            # 지우지 못하면 최대 TTL만큼 옛 값이 보인다. 쓰기를 되돌릴 이유는 없다.
            logger.warning("좋아요 수 캐시 무효화 실패 — TTL로 수렴", exc_info=True)
