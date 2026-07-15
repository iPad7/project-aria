"""아웃바운드 포트: 페르소나 영속성."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.persona.domain.model import Persona


class PersonaRepository(Protocol):
    def add(self, persona: Persona) -> None: ...

    def get_by_id(self, persona_id: UUID) -> Persona | None: ...

    def list_by_owner(self, owner_id: UUID) -> list[Persona]: ...

    def update(self, persona: Persona) -> None: ...

    def delete(self, persona: Persona) -> None: ...
