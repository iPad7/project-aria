"""PersonaRepository 포트의 SQLModel 구현."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from aria.contexts.persona.adapter.outbound.persistence.model import PersonaTable
from aria.contexts.persona.domain.model import Persona


def _to_domain(row: PersonaTable) -> Persona:
    return Persona(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        tagline=row.tagline,
        description=row.description,
        is_active=row.is_active,
    )


class SqlModelPersonaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, persona: Persona) -> None:
        self._session.add(
            PersonaTable(
                id=persona.id,
                owner_id=persona.owner_id,
                name=persona.name,
                tagline=persona.tagline,
                description=persona.description,
                is_active=persona.is_active,
            )
        )
        self._session.commit()

    def get_by_id(self, persona_id: UUID) -> Persona | None:
        row = self._session.get(PersonaTable, persona_id)
        return _to_domain(row) if row is not None else None

    def list_by_owner(self, owner_id: UUID) -> list[Persona]:
        rows = self._session.exec(
            select(PersonaTable).where(PersonaTable.owner_id == owner_id)
        ).all()
        return [_to_domain(row) for row in rows]

    def update(self, persona: Persona) -> None:
        row = self._session.get(PersonaTable, persona.id)
        if row is None:
            return
        row.name = persona.name
        row.tagline = persona.tagline
        row.description = persona.description
        row.is_active = persona.is_active
        self._session.add(row)
        self._session.commit()

    def delete(self, persona: Persona) -> None:
        row = self._session.get(PersonaTable, persona.id)
        if row is not None:
            self._session.delete(row)
            self._session.commit()
