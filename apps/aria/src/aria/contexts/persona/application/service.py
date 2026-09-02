"""persona 유스케이스. 소유권 규칙을 강제한다."""

from __future__ import annotations

from uuid import UUID

from aria.common.errors import NotFoundError, PermissionDeniedError
from aria.contexts.persona.application.port.out.repository import PersonaRepository
from aria.contexts.persona.domain.model import Persona


class PersonaService:
    def __init__(self, personas: PersonaRepository) -> None:
        self._personas = personas

    def create(
        self, owner_id: UUID, name: str, tagline: str, description: str
    ) -> Persona:
        persona = Persona(
            owner_id=owner_id, name=name, tagline=tagline, description=description
        )
        self._personas.add(persona)
        return persona

    def list_for_owner(self, owner_id: UUID) -> list[Persona]:
        return self._personas.list_by_owner(owner_id)

    def get_owned(self, owner_id: UUID, persona_id: UUID) -> Persona:
        persona = self._personas.get_by_id(persona_id)
        if persona is None:
            raise NotFoundError("페르소나를 찾을 수 없습니다", code="persona_not_found")
        if persona.owner_id != owner_id:
            raise PermissionDeniedError(
                "본인 소유의 페르소나가 아닙니다", code="not_persona_owner"
            )
        return persona

    def get_public(self, persona_id: UUID) -> Persona:
        """공개 조회 — 소유권을 묻지 않는다.

        방송국 페이지는 비로그인 시청자에게도 보여야 한다(FR-STATION-1). 관리용
        `get_owned`와 별개로 두어, 소유권 검사가 필요한 곳에서 실수로 이걸 쓰지
        않도록 이름으로 구분한다.
        """
        persona = self._personas.get_by_id(persona_id)
        if persona is None:
            raise NotFoundError("페르소나를 찾을 수 없습니다", code="persona_not_found")
        return persona

    def update(
        self,
        owner_id: UUID,
        persona_id: UUID,
        *,
        name: str | None = None,
        tagline: str | None = None,
        description: str | None = None,
    ) -> Persona:
        persona = self.get_owned(owner_id, persona_id)
        persona.edit(name=name, tagline=tagline, description=description)
        self._personas.update(persona)
        return persona

    def delete(self, owner_id: UUID, persona_id: UUID) -> None:
        persona = self.get_owned(owner_id, persona_id)
        self._personas.delete(persona)
