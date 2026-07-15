"""chat HTTP 라우터 (async).

WebSocket 전송은 이번 범위 밖 — HTTP로 조율 루프를 노출한다. 인증 주체가 작성자다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from aria.common.auth import Principal, get_current_principal
from aria.contexts.chat.adapter.inbound.http.deps import (
    get_activity_tracker,
    get_chat_service,
)
from aria.contexts.chat.adapter.inbound.http.schema import (
    MessageOutcomeResponse,
    PostMessageRequest,
    ReplyView,
    RoomStateResponse,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.service import ChatOrchestrationService

# 레거시 ActivityManager의 기본 idle 임계값(초).
_DEFAULT_IDLE_THRESHOLD = 6.0

router = APIRouter(prefix="/rooms", tags=["chat"])


@router.post("/{room_id}/messages", response_model=MessageOutcomeResponse)
async def post_message(
    room_id: UUID,
    body: PostMessageRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ChatOrchestrationService, Depends(get_chat_service)],
) -> MessageOutcomeResponse:
    outcome = await service.handle_user_message(
        room_id=room_id,
        persona_id=body.persona_id,
        author_id=principal.user_id,
        text=body.text,
    )
    reply = (
        ReplyView(text=outcome.reply.text, model_version=outcome.reply.model_version)
        if outcome.reply is not None
        else None
    )
    return MessageOutcomeResponse(accepted=outcome.accepted, reply=reply)


@router.get("/{room_id}/state", response_model=RoomStateResponse)
async def room_state(
    room_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    activity: Annotated[ActivityTracker, Depends(get_activity_tracker)],
) -> RoomStateResponse:
    return RoomStateResponse(
        idle=await activity.is_idle(room_id, _DEFAULT_IDLE_THRESHOLD),
        seconds_since_last=await activity.seconds_since_last(room_id),
    )
