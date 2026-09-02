"""persona HTTP 요청/응답 DTO."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class CreatePersonaRequest(SchemaBase):
    name: str = Field(min_length=1, max_length=30)
    tagline: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)


class UpdatePersonaRequest(SchemaBase):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    tagline: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class PersonaResponse(SchemaBase):
    id: UUID
    owner_id: UUID
    name: str
    tagline: str
    description: str
    is_active: bool


class PublicPersonaResponse(SchemaBase):
    """공개 프로필. `owner_id`를 노출하지 않는다 — 시청자가 알 필요가 없고,
    다른 컨텍스트(identity)의 식별자를 공개면으로 흘리지 않기 위해서다."""

    id: UUID
    name: str
    tagline: str
    description: str
    is_active: bool
