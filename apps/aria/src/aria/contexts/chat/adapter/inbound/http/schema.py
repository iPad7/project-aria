"""chat HTTP 요청/응답 DTO."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class PostMessageRequest(SchemaBase):
    """`persona_id` 를 받지 않는다 — **방이 페르소나를 소유한다**.

    방(#53)이 생기기 전에는 클라이언트가 매 메시지에 실어 보냈다. 지금은 시청자가
    정할 일이 아니고, 받아 두고 무시하면 잘못 보내도 아무 일이 안 일어나 디버깅이
    어려워진다(거짓 계약).
    """

    text: str = Field(min_length=1, max_length=500)


class PostSuperchatRequest(SchemaBase):
    """후원도 마찬가지로 방의 페르소나에게 간다.

    특히 여기서 중요하다 — 클라이언트가 보낸 값을 그대로 믿으면 **엉뚱한 페르소나에게
    후원이 기록된다**(`wallet_donation.persona_id`). 돈이 걸린 경로라 방에서 가져온다.
    """

    amount: int = Field(gt=0)
    message: str | None = Field(default=None, max_length=200)
    # 재연결 후 재전송이 이중 과금되지 않도록. 선택이지만 클라이언트가 주는 편이 좋다.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=64)


class MessageOutcomeResponse(SchemaBase):
    """접수 결과. 응답(reply)은 여기 없다 — 방 채널로 흐른다(C-4-1)."""

    accepted: bool


class SuperchatOutcomeResponse(SchemaBase):
    """후원 결과. 차감은 동기라 즉시 확정된다.

    감사 응답은 여기 없다. 응답이 아예 안 생겨도 후원은 성립한 것이다(차감·기록 완료).
    """

    donation_id: UUID
    balance_after: int


class OpenRoomRequest(SchemaBase):
    persona_id: UUID  # 이 방에서 방송할 스트리머
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    thumbnail_url: str | None = Field(default=None, max_length=512)


class RoomResponse(SchemaBase):
    id: UUID
    persona_id: UUID
    host_id: UUID
    name: str
    description: str | None
    thumbnail_url: str | None
    status: str


class RoomStateResponse(SchemaBase):
    idle: bool
    seconds_since_last: float | None
