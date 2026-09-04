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


class CommunicationStyleRequest(SchemaBase):
    tone: str = Field(min_length=1, max_length=100)
    sentence_length: str = Field(default="", max_length=100)
    question_style: str = Field(default="", max_length=200)
    # 1: 매우 완곡 ~ 5: 매우 직설적
    directness: int = Field(default=3, ge=1, le=5)
    empathy_expression: str = Field(default="", max_length=200)


class CoreValuesRequest(SchemaBase):
    """가치관 목록. **순서가 곧 우선순위**라 집합이 아니라 배열이다."""

    values: list[str] = Field(min_length=0, max_length=10)


class PersonaProfileResponse(SchemaBase):
    """말투·가치관. 말투를 아직 설정하지 않은 페르소나는 `style`이 null이다."""

    persona_id: UUID
    style: CommunicationStyleRequest | None
    core_values: list[str]
