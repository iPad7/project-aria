"""chat inbound DI — 전송 중립.

조율 서비스와 활동 트래커의 조립은 HTTP·WebSocket 어느 전송이든 동일하므로
전송별 폴더가 아니라 inbound 최상위에 둔다(Redis 상태 어댑터 + LLM 스텁 주입).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from aria.common.redis import get_redis
from aria.contexts.chat.adapter.outbound.inference.stub import StubPersonaLLM
from aria.contexts.chat.adapter.outbound.redis.activity import RedisActivityTracker
from aria.contexts.chat.adapter.outbound.redis.broadcast import RedisRoomBroadcaster
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
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


def get_room_broadcaster(
    redis: Annotated[Redis, Depends(get_redis)],
) -> RoomBroadcaster:
    return RedisRoomBroadcaster(redis)
