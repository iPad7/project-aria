"""Redis 클라이언트 (횡단 인프라, db.py의 형제).

실시간 상태(idle·응답 조율)의 외부 저장소. async 클라이언트를 모듈에서 만들지만
실제 연결은 지연되므로 import·/health는 Redis 없이도 뜬다. `decode_responses=True`로
값을 str로 받는다.
"""

from __future__ import annotations

from redis.asyncio import Redis

from aria.common.config import settings

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Redis:
    return redis_client
