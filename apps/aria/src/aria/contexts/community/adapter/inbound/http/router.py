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
from aria.common.errors import NotFoundError
from aria.contexts.community.adapter.inbound.http.deps import (
    get_like_service,
    get_ranking_service,
    get_story_service,
)
from aria.contexts.community.adapter.inbound.http.schema import (
    LikeCountResponse,
    StoryResponse,
    SubmitStoryRequest,
    SupporterResponse,
)
from aria.contexts.community.application.service import (
    MAX_PAGE_SIZE,
    MAX_RANKING_SIZE,
    LikeService,
    RankingService,
    StoryService,
)
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


# 좋아요는 URL상 페르소나에 매달리지만 소유 컨텍스트는 community다.
# REST 경로는 자원의 계층을 나타내는 것이지 내부 컨텍스트 경계를 나타내지 않는다.
like_router = APIRouter(prefix="/personas/{persona_id}/likes", tags=["community"])


@like_router.put("", status_code=status.HTTP_204_NO_CONTENT)
def like_persona(
    persona_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[LikeService, Depends(get_like_service)],
) -> None:
    # PUT이라 멱등하다 — 이미 눌렀어도 204. 재시도해도 안전하다.
    service.like(persona_id, principal.user_id)


@like_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def unlike_persona(
    persona_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[LikeService, Depends(get_like_service)],
) -> None:
    # DELETE도 멱등 — 안 눌린 상태에서 취소해도 204.
    service.unlike(persona_id, principal.user_id)


@like_router.get("", response_model=LikeCountResponse)
def count_likes(
    persona_id: UUID,
    service: Annotated[LikeService, Depends(get_like_service)],
) -> LikeCountResponse:
    # 공개 — 방송국 페이지가 비로그인 시청자에게도 보여야 한다(FR-STATION-1).
    return LikeCountResponse(persona_id=persona_id, count=service.count(persona_id))


@like_router.get("/me", status_code=status.HTTP_204_NO_CONTENT)
def my_like(
    persona_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[LikeService, Depends(get_like_service)],
) -> None:
    """내가 눌렀는지. 눌렀으면 204, 아니면 404."""
    if not service.liked_by(persona_id, principal.user_id):
        raise NotFoundError("좋아요를 누르지 않았습니다", code="like_not_found")


# 열혈순위. 좋아요와 같은 이유로 URL은 페르소나에 매달리고 소유 컨텍스트는 community다.
ranking_router = APIRouter(prefix="/personas/{persona_id}/ranking", tags=["community"])


@ranking_router.get("", response_model=list[SupporterResponse])
def top_supporters(
    persona_id: UUID,
    service: Annotated[RankingService, Depends(get_ranking_service)],
    limit: int = Query(default=10, ge=1, le=MAX_RANKING_SIZE),
) -> list[SupporterResponse]:
    """후원자 순위(FR-STATION-6). 공개 — 비로그인 시청자도 방송국을 본다.

    후원이 없거나 페르소나가 없으면 빈 리스트다. 404를 내지 않는 이유는 랭킹이
    페르소나의 존재를 판정하는 자리가 아니기 때문이다 — 그건 프로필 조회의 일이고,
    여기서 하려면 community가 persona를 알아야 한다.
    """
    supporters = service.top_supporters(persona_id, limit=limit)
    return [
        SupporterResponse(
            rank=s.rank,
            donor_id=s.donor_id,
            display_name=s.display_name,
            total_amount=s.total_amount,
            donation_count=s.donation_count,
        )
        for s in supporters
    ]
