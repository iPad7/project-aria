"""chat HTTP 라우터 (async).

라이브 채널은 WebSocket 전송(`../ws/router.py`)이고, 여기 HTTP는 요청-응답 형태의
단발 전송과 방 상태 조회를 제공한다(같은 조율 코어 재사용). 인증 주체가 작성자다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from aria.common.auth import Principal, get_current_principal
from aria.contexts.chat.adapter.inbound.deps import (
    get_activity_tracker,
    get_chat_service,
)
from aria.contexts.chat.adapter.inbound.http.schema import (
    MessageOutcomeResponse,
    PostMessageRequest,
    PostSuperchatRequest,
    ReplyView,
    RoomStateResponse,
    SuperchatOutcomeResponse,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.service import ChatOrchestrationService, ChatReply

# 레거시 ActivityManager의 기본 idle 임계값(초).
_DEFAULT_IDLE_THRESHOLD = 6.0

router = APIRouter(prefix="/rooms", tags=["chat"])


def _to_reply_view(reply: ChatReply | None) -> ReplyView | None:
    if reply is None:
        return None
    return ReplyView(text=reply.text, model_version=reply.model_version)


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
    return MessageOutcomeResponse(
        accepted=outcome.accepted, reply=_to_reply_view(outcome.reply)
    )


@router.post("/{room_id}/superchats", response_model=SuperchatOutcomeResponse)
async def post_superchat(
    room_id: UUID,
    body: PostSuperchatRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ChatOrchestrationService, Depends(get_chat_service)],
) -> SuperchatOutcomeResponse:
    """후원(FR-PAY-3)하고 감사 응답(FR-GEN-6)을 받는다.

    잔액이 모자라면 `InsufficientCreditError` → 409. 그 경우 차감도 기록도 없다.
    """
    outcome = await service.handle_superchat(
        room_id=room_id,
        persona_id=body.persona_id,
        donor_id=principal.user_id,
        amount=body.amount,
        message=body.message,
        idempotency_key=body.idempotency_key,
    )
    return SuperchatOutcomeResponse(
        donation_id=outcome.donation_id,
        balance_after=outcome.balance_after,
        reply=_to_reply_view(outcome.reply),
    )


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
