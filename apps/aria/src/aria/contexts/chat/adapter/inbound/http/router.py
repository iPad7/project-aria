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

from fastapi import APIRouter, Depends, Query, status

from aria.common.auth import Principal, get_current_principal, require_staff
from aria.common.config import settings
from aria.contexts.chat.adapter.inbound.deps import (
    get_activity_tracker,
    get_chat_service,
    get_room_service,
)
from aria.contexts.chat.adapter.inbound.http.schema import (
    MessageOutcomeResponse,
    OpenRoomRequest,
    PostMessageRequest,
    PostSuperchatRequest,
    RoomResponse,
    RoomStateResponse,
    SuperchatOutcomeResponse,
)
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.room import MAX_PAGE_SIZE, RoomService
from aria.contexts.chat.application.service import ChatOrchestrationService
from aria.contexts.chat.domain.room import Room, RoomStatus

router = APIRouter(prefix="/rooms", tags=["chat"])


def _to_room_response(room: Room) -> RoomResponse:
    return RoomResponse(
        id=room.id,
        persona_id=room.persona_id,
        host_id=room.host_id,
        name=room.name,
        description=room.description,
        thumbnail_url=room.thumbnail_url,
        status=room.status.value,
    )


# --- 방송 관리 (staff) ------------------------------------------------------
#
# 방 개설이 관리자 전용인 이유: chat은 persona를 import할 수 없어 "이 페르소나가
# 당신 것인가"를 확인할 수 없다. 커널 포트를 하나 더 만드는 대신 PRD FR-AUTH-3
# ("관리자는 방송·페르소나·TTS 설정을 관리한다")을 근거로 좁혔다.


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def open_room(
    body: OpenRoomRequest,
    principal: Annotated[Principal, Depends(require_staff)],
    service: Annotated[RoomService, Depends(get_room_service)],
) -> RoomResponse:
    """방을 개설한다. 아직 `pending`이라 채팅·후원은 받지 않는다."""
    room = await service.open(
        persona_id=body.persona_id,
        host_id=principal.user_id,
        name=body.name,
        description=body.description,
        thumbnail_url=body.thumbnail_url,
    )
    return _to_room_response(room)


@router.get("", response_model=list[RoomResponse])
async def list_live_rooms(
    service: Annotated[RoomService, Depends(get_room_service)],
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> list[RoomResponse]:
    """방송 중인 방 목록. 공개 — 비로그인 시청자도 무엇이 켜져 있는지 본다."""
    rooms = await service.list_live(limit=limit, offset=offset)
    return [_to_room_response(r) for r in rooms]


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: UUID,
    service: Annotated[RoomService, Depends(get_room_service)],
) -> RoomResponse:
    return _to_room_response(await service.get(room_id))


@router.post("/{room_id}/live", response_model=RoomResponse)
async def start_broadcast(
    room_id: UUID,
    principal: Annotated[Principal, Depends(require_staff)],
    service: Annotated[RoomService, Depends(get_room_service)],
) -> RoomResponse:
    """방송을 시작한다. 그 페르소나가 이미 방송 중이면 409."""
    return _to_room_response(await service.transition(room_id, RoomStatus.LIVE))


@router.post("/{room_id}/finish", response_model=RoomResponse)
async def finish_broadcast(
    room_id: UUID,
    principal: Annotated[Principal, Depends(require_staff)],
    service: Annotated[RoomService, Depends(get_room_service)],
) -> RoomResponse:
    """방송을 끝낸다. 되돌릴 수 없다 — 다시 하려면 새 방을 연다."""
    return _to_room_response(await service.transition(room_id, RoomStatus.FINISHED))


# --- 채팅·후원 --------------------------------------------------------------


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
    rooms: Annotated[RoomService, Depends(get_room_service)],
) -> MessageOutcomeResponse:
    """메시지를 접수한다 — **202**이고 응답은 여기 없다.

    생성은 워커가 하고 결과는 방 채널(Redis pub/sub)로 흘러 그 방을 구독한 모든
    연결에 간다. 응답을 보려면 WS로 붙어 있어야 한다.
    """
    await rooms.ensure_open(room_id)
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
    rooms: Annotated[RoomService, Depends(get_room_service)],
) -> SuperchatOutcomeResponse:
    """후원(FR-PAY-3)한다. 차감은 동기라 결과가 **즉시 확정**된다.

    잔액이 모자라면 `InsufficientCreditError` → 409. 그 경우 차감도 기록도 없고
    방송에도 아무 것도 나가지 않는다. 감사 응답(FR-GEN-6)은 여기 없다 — 워커가
    만들어 방 채널로 발행한다. 슬롯을 못 잡아 응답이 안 생겨도 후원은 성립한 것이다.
    """
    # 차감이 진짜로 일어나므로, 없는 방을 향한 후원은 **차감 전에** 막아야 한다.
    await rooms.ensure_open(room_id)
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
        idle=await activity.is_idle(room_id, settings.idle_threshold_seconds),
        seconds_since_last=await activity.seconds_since_last(room_id),
    )
