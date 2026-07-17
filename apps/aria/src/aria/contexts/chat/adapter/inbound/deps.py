"""chat inbound DI — 전송 중립.

조율 서비스와 활동 트래커의 조립은 HTTP·WebSocket 어느 전송이든 동일하므로
전송별 폴더가 아니라 inbound 최상위에 둔다(Redis 상태 어댑터 + LLM 어댑터 주입,
LLM은 config로 스텁/실제 생성 중 선택).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from openai import AsyncOpenAI
from redis.asyncio import Redis

from aria.common.config import settings
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.inference.openai_compat import OpenAICompatLLM
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
from aria.contexts.chat.application.port.out.llm import PersonaLLMPort
from aria.contexts.chat.application.service import ChatOrchestrationService


def _build_llm() -> PersonaLLMPort:
    # config로 스텁/실제 생성 선택. 기본 stub → 키 없는 로컬·CI 그대로 통과.
    # vLLM은 OpenAI 호환이라 base_url만 바꾸면 같은 어댑터로 자체 서빙(A-2)에도 쓴다.
    if settings.llm_backend == "openai":
        client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.openai_api_key or "not-needed",
        )
        return OpenAICompatLLM(client, settings.llm_model)
    return StubPersonaLLM()


# LLM은 무상태 → 모듈 싱글턴.
_llm = _build_llm()


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


def get_room_broadcaster(
    redis: Annotated[Redis, Depends(get_redis)],
) -> RoomBroadcaster:
    return RedisRoomBroadcaster(redis)
