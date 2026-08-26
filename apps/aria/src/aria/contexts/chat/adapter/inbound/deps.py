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
from aria.contexts.chat.adapter.outbound.inference.fallback import FallbackPersonaLLM
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


def _build_primary_llm() -> PersonaLLMPort:
    # config로 스텁/실제 생성 선택. 기본 stub → 키 없는 로컬·CI 그대로 통과.
    # vLLM은 OpenAI 호환이라 base_url만 바꾸면 같은 어댑터로 자체 서빙(A-2)에도 쓴다.
    if settings.llm_backend == "openai":
        client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.openai_api_key or "not-needed",
        )
        return OpenAICompatLLM(client, settings.llm_model)
    return StubPersonaLLM()


def _build_fallback_llm() -> PersonaLLMPort:
    # 폴백은 항상 진짜 OpenAI다(base_url을 주지 않음) — 주 백엔드가 자체 서빙일 때
    # 같은 인프라로 폴백하면 의미가 없기 때문. docs/architecture.md 참조.
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    return OpenAICompatLLM(client, settings.llm_fallback_model)


def _build_llm() -> PersonaLLMPort:
    primary = _build_primary_llm()
    if not settings.llm_fallback_enabled:
        return primary
    if not settings.openai_api_key:
        # 신뢰성 요구사항(NFR-REL-3)을 켜뒀는데 조용히 폴백 없이 도는 게 최악이다.
        # 설정 오류는 기동 시점에 시끄럽게 죽는 편이 낫다.
        raise RuntimeError(
            "ARIA_LLM_FALLBACK_ENABLED=true 인데 ARIA_OPENAI_API_KEY 가 없습니다. "
            "키를 주거나 폴백을 끄세요."
        )
    return FallbackPersonaLLM(primary, _build_fallback_llm())


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
