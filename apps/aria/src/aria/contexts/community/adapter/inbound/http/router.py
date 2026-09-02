"""community HTTP 라우터 — 방송국 사연 게시판.

작성은 인증이 필요하지만(작성자를 기록해야 하므로) **열람은 공개**다. 시청자가
로그인 없이 방송국 페이지를 둘러볼 수 있어야 하기 때문이다(FR-STATION-3).

응답은 항상 `_to_response()`를 거친다 — 익명 사연의 작성자를 감추는 변환을 한 곳에
모아, 새 엔드포인트가 실수로 원본을 노출하지 못하게 한다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from aria.common.auth import Principal, get_current_principal
from aria.contexts.community.adapter.inbound.http.deps import get_story_service
from aria.contexts.community.adapter.inbound.http.schema import (
    StoryResponse,
    SubmitStoryRequest,
)
from aria.contexts.community.application.service import MAX_PAGE_SIZE, StoryService
from aria.contexts.community.domain.model import Story

router = APIRouter(prefix="/stories", tags=["community"])


def _to_response(story: Story) -> StoryResponse:
    return StoryResponse(
        id=story.id,
        persona_id=story.persona_id,
        author_id=story.display_author_id(),  # 익명이면 None
        title=story.title,
        content=story.content,
        is_anonymous=story.is_anonymous,
        relationship_stage=story.relationship_stage,
        nickname=story.nickname,
        status=story.status.value,
    )


@router.post("", response_model=StoryResponse, status_code=status.HTTP_201_CREATED)
def submit_story(
    body: SubmitStoryRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[StoryService, Depends(get_story_service)],
) -> StoryResponse:
    story = service.submit(
        persona_id=body.persona_id,
        author_id=principal.user_id,
        title=body.title,
        content=body.content,
        is_anonymous=body.is_anonymous,
        relationship_stage=body.relationship_stage,
        nickname=body.nickname,
    )
    return _to_response(story)


@router.get("", response_model=list[StoryResponse])
def list_stories(
    service: Annotated[StoryService, Depends(get_story_service)],
    persona_id: UUID,
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> list[StoryResponse]:
    stories = service.list_for_persona(persona_id, limit=limit, offset=offset)
    return [_to_response(s) for s in stories]


@router.get("/{story_id}", response_model=StoryResponse)
def get_story(
    story_id: UUID,
    service: Annotated[StoryService, Depends(get_story_service)],
) -> StoryResponse:
    return _to_response(service.get(story_id))
