"""chat HTTP 요청/응답 DTO."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class PostMessageRequest(SchemaBase):
    persona_id: UUID  # 이 방에서 말하는 스트리머
    text: str = Field(min_length=1, max_length=500)


class PostSuperchatRequest(SchemaBase):
    persona_id: UUID
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
