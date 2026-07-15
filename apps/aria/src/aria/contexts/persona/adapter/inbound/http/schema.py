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
