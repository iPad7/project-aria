"""persona 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.persona.adapter.outbound.persistence.repository import (
    SqlModelPersonaRepository,
)
from aria.contexts.persona.application.service import PersonaService


def get_persona_service(
    session: Annotated[Session, Depends(get_session)],
) -> PersonaService:
    return PersonaService(SqlModelPersonaRepository(session))
