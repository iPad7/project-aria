"""chat HTTP 라우터 (async).

라이브 채널은 WebSocket 전송(`../ws/router.py`)이고, 여기 HTTP는 단발 전송과 방 상태
조회를 제공한다(같은 조율 코어 재사용). 인증 주체가 작성자다.

**응답(reply)은 어느 엔드포인트도 돌려주지 않는다.** C-4-1에서 생성이 워커로 빠졌기
때문이다 — 요청이 끝나는 시점에는 아직 응답이 없고, 완성되면 방 채널로 흘러 구독자
모두에게 간다. 후원의 `donation_id`·`balance_after`만 예외로 즉시 확정된다(차감은 동기).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from aria.common.auth import Principal, get_current_principal
from aria.contexts.chat.adapter.inbound.deps import (
    get_activity_tracker,
    get_chat_service,
)
from aria.contexts.chat.adapter.inbound.http.schema import (
    MessageOutcomeResponse,
    PostMessageRequest,
    PostSuperchatRequest,
    RoomStateResponse,
    SuperchatOutcomeResponse,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.service import ChatOrchestrationService

# 레거시 ActivityManager의 기본 idle 임계값(초).
_DEFAULT_IDLE_THRESHOLD = 6.0

router = APIRouter(prefix="/rooms", tags=["chat"])


@router.post(
    "/{room_id}/messages",
    response_model=MessageOutcomeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    room_id: UUID,
    body: PostMessageRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ChatOrchestrationService, Depends(get_chat_service)],
) -> MessageOutcomeResponse:
    """메시지를 접수한다 — **202**이고 응답은 여기 없다.

    생성은 워커가 하고 결과는 방 채널(Redis pub/sub)로 흘러 그 방을 구독한 모든
    연결에 간다. 응답을 보려면 WS로 붙어 있어야 한다.
    """
    outcome = await service.handle_user_message(
        room_id=room_id,
        persona_id=body.persona_id,
        author_id=principal.user_id,
        text=body.text,
    )
    return MessageOutcomeResponse(accepted=outcome.accepted)


@router.post("/{room_id}/superchats", response_model=SuperchatOutcomeResponse)
async def post_superchat(
    room_id: UUID,
    body: PostSuperchatRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ChatOrchestrationService, Depends(get_chat_service)],
) -> SuperchatOutcomeResponse:
    """후원(FR-PAY-3)한다. 차감은 동기라 결과가 **즉시 확정**된다.

    잔액이 모자라면 `InsufficientCreditError` → 409. 그 경우 차감도 기록도 없고
    방송에도 아무 것도 나가지 않는다. 감사 응답(FR-GEN-6)은 여기 없다 — 워커가
    만들어 방 채널로 발행한다. 슬롯을 못 잡아 응답이 안 생겨도 후원은 성립한 것이다.
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
        donation_id=outcome.donation_id, balance_after=outcome.balance_after
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
