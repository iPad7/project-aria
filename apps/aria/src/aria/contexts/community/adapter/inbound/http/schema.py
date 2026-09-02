"""community HTTP 요청/응답 DTO."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class SubmitStoryRequest(SchemaBase):
    persona_id: UUID
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=5000)
    is_anonymous: bool = True
    relationship_stage: str | None = Field(default=None, max_length=50)
    nickname: str | None = Field(default=None, max_length=50)


class StoryResponse(SchemaBase):
    """응답 DTO.

    `author_id`는 도메인의 `display_author_id()`를 거쳐 채운다 — 익명 사연은
    작성자가 나가지 않는다. 저장된 원본은 그대로 두고 표시만 감추는 방식이라,
    변환을 라우터가 아니라 이 경계에서 일관되게 해야 한다.
    """

    id: UUID
    persona_id: UUID
    author_id: UUID | None
    title: str
    content: str
    is_anonymous: bool
    relationship_stage: str | None
    nickname: str | None
    status: str
