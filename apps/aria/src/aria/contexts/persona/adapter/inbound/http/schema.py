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


class MoralCompassRequest(SchemaBase):
    """도덕 나침반. 셋 다 자유 문장이다 — 그대로 프롬프트가 된다."""

    standard: str = Field(min_length=1, max_length=200)
    rule_adherence: str = Field(default="", max_length=200)
    fairness: str = Field(default="", max_length=200)


class CoreValuesRequest(SchemaBase):
    """가치관 목록. **순서가 곧 우선순위**라 집합이 아니라 배열이다."""

    values: list[str] = Field(min_length=0, max_length=10)


class PersonaProfileResponse(SchemaBase):
    """말투·나침반·가치관. 아직 설정하지 않은 축은 null이다 — 셋은 서로 독립이라
    말투만 정하고 나침반은 비워 둘 수 있다."""

    persona_id: UUID
    style: CommunicationStyleRequest | None
    compass: MoralCompassRequest | None
    core_values: list[str]
