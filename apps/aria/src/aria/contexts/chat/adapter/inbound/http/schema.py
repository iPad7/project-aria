"""chat HTTP 요청/응답 DTO."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class PostMessageRequest(SchemaBase):
    persona_id: UUID  # 이 방에서 말하는 스트리머
    text: str = Field(min_length=1, max_length=500)


class ReplyView(SchemaBase):
    text: str
    model_version: str | None


class MessageOutcomeResponse(SchemaBase):
    accepted: bool
    reply: ReplyView | None  # AI가 바쁘면 null (메시지는 accepted)


class RoomStateResponse(SchemaBase):
    idle: bool
    seconds_since_last: float | None
