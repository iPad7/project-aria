"""generation-worker 합성 루트.

`aria/app.py`가 api의 합성 루트인 것처럼 여기는 워커의 합성 루트다. 둘 다 common과
컨텍스트를 함께 아는 자리라 common 밖 최상위에 둔다(커널 순수성 계약 유지).

같은 이미지로 뜨되 진입점만 다르다 — `docs/architecture.md`의 "api /
generation-worker / media-worker (같은 이미지)" 그대로다.

실행: `uv run faststream run aria.workers.generation:app`
"""

from __future__ import annotations

from faststream import FastStream

from aria.common.config import settings
from aria.common.kafka import get_broker
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.inbound.worker import router as worker_router
from aria.contexts.chat.adapter.outbound.inference.factory import build_llm
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.generation import ResponseGenerationService


def create_app() -> FastStream:
    broker = get_broker()
    redis = get_redis()

    # 워커는 DB를 모른다 — 슬롯(Redis)·생성(포트 뒤)·발행(Redis pub/sub)이 전부다.
    # 응답이 pub/sub으로 나가므로 api 프로세스의 WS 연결들이 그대로 받는다.
    service = ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=build_llm(),
        broadcaster=RedisRoomBroadcaster(redis),
    )
    worker_router.register(broker, service, group_id=settings.generation_consumer_group)
    return FastStream(broker)


app = create_app()
