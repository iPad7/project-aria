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


class ReplyView(SchemaBase):
    text: str
    model_version: str | None


class MessageOutcomeResponse(SchemaBase):
    accepted: bool
    reply: ReplyView | None  # AI가 바쁘면 null (메시지는 accepted)


class SuperchatOutcomeResponse(SchemaBase):
    """후원 결과. `reply`가 null이어도 후원은 성립했다(차감·기록 완료)."""

    donation_id: UUID
    balance_after: int
    reply: ReplyView | None


class RoomStateResponse(SchemaBase):
    idle: bool
    seconds_since_last: float | None
