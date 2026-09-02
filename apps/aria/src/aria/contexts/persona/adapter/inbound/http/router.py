"""persona HTTP 라우터.

두 종류가 있다. 대부분은 **크리에이터용 관리 API**로 소유권을 요구하고,
`/{id}/profile` 하나만 **시청자용 공개 조회**다(FR-STATION-1) — 방송국 페이지가
비로그인 시청자에게도 보여야 하기 때문이다.

인증 주체는 공통 authN(get_current_principal)에서 온다. persona는 identity를 모른다.
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
    PublicPersonaResponse,
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


@router.get("/{persona_id}/profile", response_model=PublicPersonaResponse)
def get_persona_profile(
    persona_id: UUID,
    service: Annotated[PersonaService, Depends(get_persona_service)],
) -> Persona:
    """방송국 공개 프로필 — 인증 없이 열람 가능(FR-STATION-1).

    관리용 `GET /personas/{id}`는 소유자만 볼 수 있고 그대로 둔다. 좋아요 수는
    community가 별도로 노출하며(`GET /personas/{id}/likes`), 두 응답의 합성은
    프론트가 한다 — 단순 조회 합성이라 컨텍스트 간 포트를 만들 값을 못 한다.
    """
    return service.get_public(persona_id)


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
