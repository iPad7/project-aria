"""chat 조립 — Redis 상태 어댑터와 LLM 스텁을 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.service import ChatOrchestrationService

# LLM 스텁은 무상태 → 모듈 싱글턴. 실제 추론 클라이언트로 교체되는 지점.
_llm = StubPersonaLLM()


def get_activity_tracker(
    redis: Annotated[Redis, Depends(get_redis)],
) -> ActivityTracker:
    return RedisActivityTracker(redis)


def get_chat_service(
    redis: Annotated[Redis, Depends(get_redis)],
) -> ChatOrchestrationService:
    return ChatOrchestrationService(
        activity=RedisActivityTracker(redis),
        coordinator=RedisResponseCoordinator(redis),
        llm=_llm,
    )
