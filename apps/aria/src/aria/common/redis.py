"""Redis 클라이언트 (횡단 인프라, db.py의 형제).

실시간 상태(idle·응답 조율)의 외부 저장소. async 클라이언트를 모듈에서 만들지만
실제 연결은 지연되므로 import·/health는 Redis 없이도 뜬다. `decode_responses=True`로
값을 str로 받는다.
"""

from __future__ import annotations

import redis as sync_redis
from redis.asyncio import Redis

from aria.common.config import settings

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Redis:
    return redis_client


# sync 클라이언트도 둔다. chat은 async 컨텍스트라 위 클라이언트를 쓰지만,
# persona·community의 영속 계층은 sync(SQLModel Session)다. sync 핸들러에서 async
# 클라이언트를 await할 수 없고, async 핸들러에서 sync DB를 호출하면 이벤트 루프를
# 막는다. FastAPI가 sync 핸들러를 스레드풀에서 돌리므로 sync 쪽에는 sync 클라이언트가
# 맞다. 연결은 마찬가지로 지연된다.
sync_redis_client: sync_redis.Redis = sync_redis.Redis.from_url(
    settings.redis_url, decode_responses=True
)


def get_sync_redis() -> sync_redis.Redis:
    return sync_redis_client
