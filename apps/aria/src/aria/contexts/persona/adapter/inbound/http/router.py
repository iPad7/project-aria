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
from aria.contexts.persona.adapter.inbound.http.deps import (
    get_persona_service,
    get_profile_service,
)
from aria.contexts.persona.adapter.inbound.http.schema import (
    CommunicationStyleRequest,
    CoreValuesRequest,
    CreatePersonaRequest,
    PersonaProfileResponse,
    PersonaResponse,
    PublicPersonaResponse,
    UpdatePersonaRequest,
)
from aria.contexts.persona.application.service import (
    PersonaProfileService,
    PersonaService,
)
from aria.contexts.persona.domain.model import CommunicationStyle, Persona

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


# --- 인격 (말투·가치관) -----------------------------------------------------
#
# 이 값들이 `PersonaProfilePort`를 거쳐 생성의 시스템 프롬프트가 된다. 조회는 공개
# (방송국 페이지가 "이 스트리머는 이런 사람"을 보여준다), 수정은 소유자만.
#
# 경로가 `/profile`이 아니라 `/voice`인 이유: 위쪽 `/profile`은 이미 방송국 공개
# 프로필(이름·소개)이다. 여기는 "어떻게 말하는가"라 다른 것이다.


def _to_profile_response(
    persona_id: UUID, style: CommunicationStyle | None, core_values: list[str]
) -> PersonaProfileResponse:
    return PersonaProfileResponse(
        persona_id=persona_id,
        style=(
            CommunicationStyleRequest(
                tone=style.tone,
                sentence_length=style.sentence_length,
                question_style=style.question_style,
                directness=style.directness,
                empathy_expression=style.empathy_expression,
            )
            if style is not None
            else None
        ),
        core_values=core_values,
    )


@router.get("/{persona_id}/voice", response_model=PersonaProfileResponse)
def get_persona_voice(
    persona_id: UUID,
    service: Annotated[PersonaProfileService, Depends(get_profile_service)],
) -> PersonaProfileResponse:
    style, core_values = service.get(persona_id)
    return _to_profile_response(persona_id, style, core_values)


@router.put("/{persona_id}/style", response_model=PersonaProfileResponse)
def set_communication_style(
    persona_id: UUID,
    body: CommunicationStyleRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaProfileService, Depends(get_profile_service)],
) -> PersonaProfileResponse:
    """말투를 설정한다. PUT이라 멱등 — 원하는 최종 상태를 보낸다."""
    style = service.set_style(
        principal.user_id,
        persona_id,
        tone=body.tone,
        sentence_length=body.sentence_length,
        question_style=body.question_style,
        directness=body.directness,
        empathy_expression=body.empathy_expression,
    )
    _, core_values = service.get(persona_id)
    return _to_profile_response(persona_id, style, core_values)


@router.put("/{persona_id}/core-values", response_model=PersonaProfileResponse)
def set_core_values(
    persona_id: UUID,
    body: CoreValuesRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PersonaProfileService, Depends(get_profile_service)],
) -> PersonaProfileResponse:
    """가치관을 **통째로 교체**한다. 배열 순서가 우선순위다."""
    core_values = service.set_core_values(principal.user_id, persona_id, body.values)
    style, _ = service.get(persona_id)
    return _to_profile_response(persona_id, style, core_values)
