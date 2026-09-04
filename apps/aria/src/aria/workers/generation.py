"""generation-worker 합성 루트.

`aria/app.py`가 api의 합성 루트인 것처럼 여기는 워커의 합성 루트다. 둘 다 common과
컨텍스트를 함께 아는 자리라 common 밖 최상위에 둔다(커널 순수성 계약 유지).

같은 이미지로 뜨되 진입점만 다르다 — `docs/architecture.md`의 "api /
generation-worker / media-worker (같은 이미지)" 그대로다.

실행: `uv run faststream run aria.workers.generation:app`
"""

from __future__ import annotations

from faststream import FastStream
from sqlmodel import Session

from aria.common.config import settings
from aria.common.db import engine
from aria.common.kafka import KafkaEventBus, get_broker
from aria.common.langfuse_tracing import build_tracing
from aria.common.redis import get_redis
from aria.common.topics import ensure_topics
from aria.contexts.chat.adapter.inbound.worker import router as worker_router
from aria.contexts.chat.adapter.inbound.worker.router import (
    DLQ_SUFFIX,
    GenerationConsumer,
)
from aria.contexts.chat.adapter.outbound.inference.factory import build_llm
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.adapter.outbound.redis.dedup import RedisProcessedRegistry
from aria.contexts.chat.application.generation import (
    RESPONSE_REQUESTED,
    SUPERCHAT_REQUESTED,
    ResponseGenerationService,
)
from aria.contexts.persona.adapter.outbound.cache.profile import CachedPersonaProfiles
from aria.contexts.persona.adapter.outbound.persistence.repository import (
    SqlModelPersonaRepository,
    SqlModelProfileRepository,
)
from aria.contexts.persona.adapter.outbound.profile import PersonaProfileProvider


def create_app() -> FastStream:
    broker = get_broker()
    redis = get_redis()

    # 페르소나 인격을 읽으려고 **DB를 알게 됐다**. C-4에서 "워커는 DB를 모른다"고 한 것은
    # 그때 읽을 것이 없었기 때문이고, 지금은 생성에 인격이 필요하다. 읽기 전용이며
    # 캐시가 앞에 있어 대부분의 생성은 DB까지 가지 않는다.
    session = Session(engine)
    profiles = CachedPersonaProfiles(
        PersonaProfileProvider(
            SqlModelPersonaRepository(session), SqlModelProfileRepository(session)
        ),
        redis,
    )

    # 관측은 기본이 no-op이다 — 키 없는 로컬·CI가 그대로 돈다.
    tracing = build_tracing()
    service = ResponseGenerationService(
        coordinator=RedisResponseCoordinator(redis),
        llm=build_llm(tracing),
        broadcaster=RedisRoomBroadcaster(redis),
        profiles=profiles,
        tracing=tracing,
    )
    # 배달 보증(멱등·DLQ)은 어댑터가 입힌다 — 위 서비스는 그런 게 있는 줄 모른다.
    consumer = GenerationConsumer(
        service,
        RedisProcessedRegistry(redis, ttl_seconds=settings.dedup_ttl_seconds),
        KafkaEventBus(broker),
    )
    worker_router.register(
        broker, consumer, group_id=settings.generation_consumer_group
    )

    app = FastStream(broker)

    @app.on_shutdown
    async def flush_traces() -> None:
        # 버퍼에 남은 관측을 내보낸다. 안 하면 마지막 몇 건이 사라진다.
        tracing.flush()

    @app.on_startup
    async def declare_topics() -> None:
        # 구독이 시작되기 전에 파티션 수를 못박는다 — 자동 생성에 맡기면 1개가 되어
        # 워커를 여럿 띄워도 하나만 일한다.
        await ensure_topics(
            [
                RESPONSE_REQUESTED,
                SUPERCHAT_REQUESTED,
                RESPONSE_REQUESTED + DLQ_SUFFIX,
                SUPERCHAT_REQUESTED + DLQ_SUFFIX,
            ]
        )

    return app


app = create_app()
