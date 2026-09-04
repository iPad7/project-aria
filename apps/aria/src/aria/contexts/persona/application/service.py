"""persona 유스케이스. 소유권 규칙을 강제한다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from aria.common.errors import NotFoundError, PermissionDeniedError, ValidationError
from aria.contexts.persona.application.port.out.repository import (
    PersonaRepository,
    ProfileRepository,
)
from aria.contexts.persona.domain.model import CommunicationStyle, Persona


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


class PersonaProfileService:
    """말투·가치관 관리.

    `PersonaService`와 나눈 이유: 프로필 설정은 페르소나 CRUD와 쓰임새가 다르고,
    무엇보다 **캐시 무효화라는 부수효과**를 갖는다. 그걸 CRUD 서비스에 섞으면
    페르소나를 만들 때마다 캐시를 신경 쓰게 된다.

    소유권 검사는 `PersonaService.get_owned`를 그대로 쓴다 — 내 페르소나의 말투만
    바꿀 수 있다.
    """

    def __init__(
        self,
        personas: PersonaService,
        profiles: ProfileRepository,
        on_change: Callable[[UUID], None] | None = None,
    ) -> None:
        self._personas = personas
        self._profiles = profiles
        # 캐시 무효화 훅. 열혈순위와 달리 **쓰기도 이 컨텍스트의 것**이라 걸 수 있다.
        self._on_change = on_change

    def get(self, persona_id: UUID) -> tuple[CommunicationStyle | None, list[str]]:
        self._personas.get_public(persona_id)  # 없는 페르소나면 여기서 404
        return (
            self._profiles.get_style(persona_id),
            self._profiles.list_core_values(persona_id),
        )

    def set_style(
        self,
        owner_id: UUID,
        persona_id: UUID,
        *,
        tone: str,
        sentence_length: str = "",
        question_style: str = "",
        directness: int = 3,
        empathy_expression: str = "",
    ) -> CommunicationStyle:
        self._personas.get_owned(owner_id, persona_id)
        style = CommunicationStyle(
            persona_id=persona_id,
            tone=tone,
            sentence_length=sentence_length,
            question_style=question_style,
            directness=directness,
            empathy_expression=empathy_expression,
        )
        self._profiles.set_style(style)
        self._invalidate(persona_id)
        return style

    def set_core_values(
        self, owner_id: UUID, persona_id: UUID, value_names: Sequence[str]
    ) -> list[str]:
        self._personas.get_owned(owner_id, persona_id)
        # 순서가 곧 우선순위이므로 통째로 교체한다. 중복은 거부한다 — 같은 가치를
        # 두 번 매다는 것은 우선순위를 두 개 갖겠다는 뜻이라 말이 안 된다.
        if len(set(value_names)) != len(value_names):
            raise ValidationError("가치관이 중복됩니다", code="duplicate_core_value")
        self._profiles.set_core_values(persona_id, value_names)
        self._invalidate(persona_id)
        return list(value_names)

    def _invalidate(self, persona_id: UUID) -> None:
        if self._on_change is not None:
            self._on_change(persona_id)
