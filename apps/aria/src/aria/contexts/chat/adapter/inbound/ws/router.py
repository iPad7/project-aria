"""chat WebSocket 전송 (async).

라이브 채널. 조율 코어(ChatOrchestrationService)는 그대로 재사용하고 전송만 WS로
바꾼다 — HTTP POST가 요청-응답 1회라면, 여기선 한 연결 위에서 메시지 루프를 돈다.

인증은 '첫 프레임' 방식: 연결 수락 후 클라이언트가 첫 메시지로 {"token": ...}를 보내면
common의 principal_from_token으로 검증한다(토큰이 URL·로그에 남지 않음). 실패·타임아웃이면
애플리케이션 close 코드로 끊는다.

이 슬라이스는 보낸 클라이언트에게만 응답을 돌려준다. 방의 다른 접속자에게 퍼뜨리는
pub/sub 팬아웃은 다음 슬라이스에서 이 연결 위에 얹는다.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from aria.common.auth import Principal, principal_from_token
from aria.common.errors import UnauthorizedError
from aria.contexts.chat.adapter.inbound.deps import get_chat_service
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
) -> None:
    await websocket.accept()

    principal = await _authenticate(websocket)
    if principal is None:
        return

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
                # 잘못된 프레임은 연결을 끊지 않고 에러 프레임으로 알린다.
                await websocket.send_json(
                    {"error": {"code": "invalid_message", "message": str(exc)}}
                )
                continue

            reply = (
                {
                    "text": outcome.reply.text,
                    "model_version": outcome.reply.model_version,
                }
                if outcome.reply is not None
                else None
            )
            await websocket.send_json({"accepted": outcome.accepted, "reply": reply})
    except WebSocketDisconnect:
        return
