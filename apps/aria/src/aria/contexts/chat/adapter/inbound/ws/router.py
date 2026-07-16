"""chat WebSocket 전송 (async, pub/sub 팬아웃).

라이브 채널. 조율 코어(ChatOrchestrationService)는 그대로 재사용하고 전송만 WS로 바꾼다.

인증은 '첫 프레임' 방식: 연결 수락 후 클라이언트가 첫 메시지로 {"token": ...}를 보내면
common의 principal_from_token으로 검증한다(토큰이 URL·로그에 남지 않음). 실패·타임아웃이면
애플리케이션 close 코드로 끊는다.

응답을 보낸 사람에게만 돌려주지 않는다 — 메시지·응답을 방 토픽에 발행하고, 그 방을
구독한 모든 연결(프로세스를 넘어)로 흘린다. 그래서 연결마다 두 펌프를 동시에 돌린다:
inbound(수신 → 조율 코어 → 발행), outbound(구독 스트림 → 소켓). 구독을 발행보다 먼저
성립시켜(유실 방지) 보낸 사람도 자기 메시지를 스트림으로 되받는다(모두 같은 이벤트 순서).
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from aria.common.auth import Principal, principal_from_token
from aria.common.errors import UnauthorizedError
from aria.contexts.chat.adapter.inbound.deps import (
    get_chat_service,
    get_room_broadcaster,
)
from aria.contexts.chat.application.port.out.broadcast import RoomBroadcaster
from aria.contexts.chat.application.service import ChatOrchestrationService

# 애플리케이션 정의 close 코드(4000~4999).
_CLOSE_UNAUTHORIZED = 4401
_CLOSE_AUTH_TIMEOUT = 4408
# 첫 프레임(auth)을 기다리는 시간(초) — 미인증 연결이 오래 매달리지 않게.
_AUTH_TIMEOUT = 5.0

router = APIRouter(prefix="/rooms", tags=["chat"])


async def _authenticate(websocket: WebSocket) -> Principal | None:
    """첫 프레임에서 토큰을 받아 검증. 실패면 소켓을 닫고 None."""
    try:
        frame = await asyncio.wait_for(websocket.receive_json(), timeout=_AUTH_TIMEOUT)
        return principal_from_token(frame["token"])
    except (TimeoutError, asyncio.TimeoutError):
        await websocket.close(code=_CLOSE_AUTH_TIMEOUT)
    except (UnauthorizedError, KeyError, ValueError, TypeError):
        await websocket.close(code=_CLOSE_UNAUTHORIZED)
    except WebSocketDisconnect:
        pass  # 클라이언트가 먼저 끊음 — 조용히 종료
    return None


@router.websocket("/{room_id}/ws")
async def chat_ws(
    websocket: WebSocket,
    room_id: UUID,
    service: Annotated[ChatOrchestrationService, Depends(get_chat_service)],
    broadcaster: Annotated[RoomBroadcaster, Depends(get_room_broadcaster)],
) -> None:
    await websocket.accept()

    principal = await _authenticate(websocket)
    if principal is None:
        return

    # 구독을 먼저 성립시킨다 — 이후 발행분(자기 메시지 포함)을 유실 없이 받는다.
    stream = await broadcaster.subscribe(room_id)

    # anyio 태스크 그룹으로 두 펌프를 돌린다 — Starlette가 anyio 위에서 돌므로
    # 구조적 취소를 따라야 teardown이 깨끗하다(raw asyncio.create_task는 스코프 밖).
    async def pump_outbound() -> None:
        try:
            async with aclosing(stream):
                async for event in stream:
                    await websocket.send_json(event)
        except WebSocketDisconnect:
            pass  # 소켓이 먼저 닫힘 — 취소로 정리된다

    async def pump_inbound(task_group: anyio.abc.TaskGroup) -> None:
        try:
            while True:
                data = await websocket.receive_json()
                try:
                    persona_id = UUID(data["persona_id"])
                    text = data["text"]
                    outcome = await service.handle_user_message(
                        room_id=room_id,
                        persona_id=persona_id,
                        author_id=principal.user_id,
                        text=text,
                    )
                except (KeyError, ValueError, ValidationError) as exc:
                    # 잘못된 프레임은 끊지 않고 보낸 사람에게만 에러로 알린다(발행 안 함).
                    await websocket.send_json(
                        {"error": {"code": "invalid_message", "message": str(exc)}}
                    )
                    continue

                await broadcaster.publish(
                    room_id,
                    {
                        "type": "message",
                        "room_id": str(room_id),
                        "author_id": str(principal.user_id),
                        "text": text,
                    },
                )
                if outcome.reply is not None:
                    await broadcaster.publish(
                        room_id,
                        {
                            "type": "reply",
                            "room_id": str(room_id),
                            "persona_id": str(persona_id),
                            "text": outcome.reply.text,
                            "model_version": outcome.reply.model_version,
                        },
                    )
        except WebSocketDisconnect:
            pass  # 클라이언트 종료 — outbound 펌프까지 취소하고 핸들러를 끝낸다
        finally:
            task_group.cancel_scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(pump_outbound)
        tg.start_soon(pump_inbound, tg)
