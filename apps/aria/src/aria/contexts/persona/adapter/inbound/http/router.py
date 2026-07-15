"""persona HTTP 라우터 — 크리에이터용 관리 API(소유권 기반).

인증 주체는 공통 authN(get_current_principal)에서 온다. persona는 identity를 모른다.
공개 열람(비인증)은 후속 read model에서 다룬다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from aria.common.auth import Principal, get_current_principal
from aria.contexts.persona.adapter.inbound.http.deps import get_persona_service
from aria.contexts.persona.adapter.inbound.http.schema import (
    CreatePersonaRequest,
    PersonaResponse,
    UpdatePersonaRequest,
)
from aria.contexts.persona.application.service import PersonaService
from aria.contexts.persona.domain.model import Persona

router = APIRouter(prefix="/personas", tags=["persona"])


@router.post("", response_model=PersonaResponse, status_code=status.HTTP_201_CREATED)
def create_persona(
    body: CreatePersonaRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> Persona:
    return service.create(principal.user_id, body.name, body.tagline, body.description)


@router.get("", response_model=list[PersonaResponse])
def list_my_personas(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> list[Persona]:
    return service.list_for_owner(principal.user_id)


@router.get("/{persona_id}", response_model=PersonaResponse)
def get_persona(
    persona_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> Persona:
    return service.get_owned(principal.user_id, persona_id)


@router.patch("/{persona_id}", response_model=PersonaResponse)
def update_persona(
    persona_id: UUID,
    body: UpdatePersonaRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> Persona:
    return service.update(
        principal.user_id,
        persona_id,
        name=body.name,
        tagline=body.tagline,
        description=body.description,
    )


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_persona(
    persona_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> None:
    service.delete(principal.user_id, persona_id)
