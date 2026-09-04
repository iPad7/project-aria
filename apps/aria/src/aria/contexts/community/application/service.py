"""community 유스케이스 — 사연 게시판.

persona 슬라이스와 달리 소유권 검사가 없다. 사연은 **누구나 쓰고 누구나 읽는**
공개 게시판이기 때문이다(FR-STATION-2/3). 작성자는 인증 주체에서 오고, 표시 여부는
`is_anonymous`가 결정한다.
"""

from __future__ import annotations

from uuid import UUID

from aria.common.errors import NotFoundError
from aria.common.ranking import DonationRankingPort
from aria.common.user_directory import UserDirectoryPort
from aria.contexts.community.application.port.out.repository import (
    LikeRepository,
    StoryRepository,
)
from aria.contexts.community.domain.model import Story, Supporter

# 게시판 한 페이지 크기의 상한. 무한정 긁어가는 것을 막는다.
MAX_PAGE_SIZE = 100

# 열혈순위 한 번에 내려주는 최대 인원. 게시판보다 훨씬 작다 — 순위표는 상위 몇 명을
# 보는 화면이고, 여기를 키우면 집계 비용과 캐시 키가 함께 늘어난다.
MAX_RANKING_SIZE = 50


class StoryService:
    def __init__(self, stories: StoryRepository) -> None:
        self._stories = stories

    def submit(
        self,
        persona_id: UUID,
        author_id: UUID,
        title: str,
        content: str,
        *,
        is_anonymous: bool = True,
        relationship_stage: str | None = None,
        nickname: str | None = None,
    ) -> Story:
        story = Story(
            persona_id=persona_id,
            author_id=author_id,
            title=title,
            content=content,
            is_anonymous=is_anonymous,
            relationship_stage=relationship_stage,
            nickname=nickname,
        )
        self._stories.add(story)
        return story

    def list_for_persona(
        self, persona_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[Story]:
        return self._stories.list_by_persona(
            persona_id, limit=min(limit, MAX_PAGE_SIZE), offset=max(offset, 0)
        )

    def get(self, story_id: UUID) -> Story:
        story = self._stories.get_by_id(story_id)
        if story is None:
            raise NotFoundError("사연을 찾을 수 없습니다", code="story_not_found")
        return story


class LikeService:
    """좋아요 유스케이스.

    좋아요/취소는 멱등이라 "이미 눌렀음" 같은 예외를 만들지 않는다. 클라이언트가
    재시도해도 안전해야 하기 때문이다(FR-STATION-5의 '토글'은 UI의 표현이고,
    API는 원하는 최종 상태를 선언하는 형태로 둔다).
    """

    def __init__(self, likes: LikeRepository) -> None:
        self._likes = likes

    def like(self, persona_id: UUID, user_id: UUID) -> None:
        self._likes.add(persona_id, user_id)

    def unlike(self, persona_id: UUID, user_id: UUID) -> None:
        self._likes.remove(persona_id, user_id)

    def liked_by(self, persona_id: UUID, user_id: UUID) -> bool:
        return self._likes.exists(persona_id, user_id)

    def count(self, persona_id: UUID) -> int:
        return self._likes.count_by_persona(persona_id)


class RankingService:
    """열혈순위 유스케이스 — 방송국의 후원자 순위(FR-STATION-6).

    community가 소유하지만 데이터는 하나도 갖고 있지 않다. 후원 금액은 wallet이,
    표시명은 identity가 갖고 있고 여기는 **둘을 합쳐 순위표라는 화면 개념을 만든다**.
    그래서 리포지토리가 아니라 두 개의 커널 포트를 받는다.

    합치는 일을 굳이 community가 하는 이유는 방송국 페이지가 community의 것이기
    때문이다 — 순위·좋아요·사연이 한 컨텍스트에서 조립돼야 화면의 규칙(탈퇴자 표시,
    상한, 동점 처리)이 한 곳에 모인다.
    """

    def __init__(
        self, ranking: DonationRankingPort, directory: UserDirectoryPort
    ) -> None:
        self._ranking = ranking
        self._directory = directory

    def top_supporters(self, persona_id: UUID, *, limit: int = 10) -> list[Supporter]:
        ranks = self._ranking.top_donors(
            persona_id, limit=min(max(limit, 1), MAX_RANKING_SIZE)
        )
        if not ranks:
            # 이름 조회를 건너뛴다 — 후원이 없는 방송국이 흔하고, 빈 조회를 굳이
            # 왕복시킬 이유가 없다.
            return []

        # 이름은 **한 번에** 가져온다. 순위 한 줄마다 조회하면 N+1이 된다.
        names = self._directory.display_names([r.donor_id for r in ranks])
        return [
            Supporter(
                rank=position,
                donor_id=rank.donor_id,
                # 탈퇴한 사용자는 이름이 없다. 순위에서 빼지는 않는다 — 실제로 그만큼
                # 후원한 사람이 있었고, 지우면 아래 순위가 한 칸씩 올라가 버린다.
                display_name=names.get(rank.donor_id),
                total_amount=rank.total_amount,
                donation_count=rank.donation_count,
            )
            for position, rank in enumerate(ranks, start=1)
        ]
