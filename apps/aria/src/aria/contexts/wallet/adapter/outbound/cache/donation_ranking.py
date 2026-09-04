"""열혈순위 캐시 데코레이터.

랭킹은 방송국 페이지가 시청자마다 부르는 핫 리드다. 그런데 좋아요 수와 달리
`GROUP BY donor_id + SUM + ORDER BY`라 인덱스가 있어도 한 페르소나의 후원 로우를
전부 읽어 집계해야 한다 — 후원이 쌓일수록 선형으로 느려진다. 그 앞에 캐시를 둔다.

**DB가 진실이고 Redis는 캐시일 뿐이다.** 순위를 Redis에 누적하지 않으므로(ZSET 같은
것을 쓰지 않는다) 캐시가 비어 있어도 다음 조회가 집계로 복구한다.

**무효화 훅이 없다 — TTL만으로 수렴한다.** 후원 기록은 `WalletRepository.apply()`가
차감과 같은 트랜잭션에서 쓰므로 이 데코레이터를 거치지 않는다. 좋아요는 쓰기와 읽기가
같은 리포지토리라 지울 수 있었지만 여기는 아니다. 대신 순위가 최대 TTL만큼 늦게
반영되는데, 이건 감수한다 — 순위표에서 몇 초의 지연은 오차 범위이고, 반대로 무효화
훅을 만들자고 차감 경로에 캐시 의존을 끼워 넣으면 결제 트랜잭션이 Redis 장애에
묶인다.

`DonationRepository`를 구현하면서 `DonationRepository`를 감싼다 — 좋아요 수 캐시와
같은 데코레이터 패턴이라 어댑터도 서비스도 캐시의 존재를 모른다.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from redis import Redis, RedisError

from aria.contexts.wallet.application.port.out.repository import DonationRepository
from aria.contexts.wallet.domain.model import Donation, DonorTotal

logger = logging.getLogger(__name__)

# 새 후원이 순위에 반영되기까지의 최대 지연(초).
_TTL_SECONDS = 30


def _key(persona_id: UUID, limit: int) -> str:
    # limit이 키에 들어간다 — 상위 10개를 캐시해 두고 상위 50개 요청에 답하면
    # 없는 사람을 빠뜨린 순위를 주게 된다.
    return f"wallet:donation_ranking:{persona_id}:{limit}"


class CachedDonationRepository:
    def __init__(self, inner: DonationRepository, redis: Redis) -> None:
        self._inner = inner
        self._redis = redis

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Donation]:
        # 목록은 인덱스 순서대로 limit개만 읽는다 — 이미 싸므로 캐시하지 않는다.
        return self._inner.list_by_persona(persona_id, limit=limit, offset=offset)

    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorTotal]:
        cached = self._get(persona_id, limit)
        if cached is not None:
            return cached
        ranking = self._inner.top_donors(persona_id, limit=limit)
        self._set(persona_id, limit, ranking)
        return ranking

    # --- Redis 접근. 캐시 장애가 기능을 멈추게 하지 않는다 -----------------------
    #
    # Redis가 죽어도 순위는 계속 보여야 한다. 캐시는 성능 장치이지 정확성의 일부가
    # 아니므로, 실패하면 로그만 남기고 DB 경로로 계속 간다(좋아요 수 캐시와 같다).

    def _get(self, persona_id: UUID, limit: int) -> list[DonorTotal] | None:
        try:
            raw = self._redis.get(_key(persona_id, limit))
        except RedisError:
            logger.warning("열혈순위 캐시 조회 실패 — DB로 폴백", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return [DonorTotal(**item) for item in json.loads(raw)]
        except (ValueError, TypeError):
            # 손상됐거나 형식이 바뀐 값. 지우고 DB에서 다시 읽는다.
            self._invalidate(persona_id, limit)
            return None

    def _set(self, persona_id: UUID, limit: int, ranking: list[DonorTotal]) -> None:
        payload = json.dumps([r.model_dump(mode="json") for r in ranking])
        try:
            self._redis.set(_key(persona_id, limit), payload, ex=_TTL_SECONDS)
        except RedisError:
            logger.warning("열혈순위 캐시 기록 실패 — 무시", exc_info=True)

    def _invalidate(self, persona_id: UUID, limit: int) -> None:
        try:
            self._redis.delete(_key(persona_id, limit))
        except RedisError:
            logger.warning("열혈순위 캐시 무효화 실패 — TTL로 수렴", exc_info=True)
