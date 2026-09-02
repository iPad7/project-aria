"""community 도메인 모델 — 방송국(팬덤 채널)의 사연.

레거시가 `chat.Story`와 `influencers.Story`로 중복 정의했던 것을 여기 하나로 모은다.
사연은 `community`가 소유하고, `chat`의 idle 낭독은 읽기 포트로 소비한다
(`docs/events.md`) — 그래서 상태 전이(pending→reading→done)도 이쪽 도메인의 일이다.

`persona_id`·`author_id`는 다른 컨텍스트의 엔티티를 가리키지만 **불투명 UUID**일 뿐이다.
community는 persona도 identity도 import하지 않는다.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import Field

from aria.common.domain import Entity


class StoryStatus(Enum):
    """idle 낭독 진행 상태.

    pending: 낭독 대기 · reading: 낭독 중(claim됨) · done: 낭독 완료.
    실제 전이는 B-3의 StoryFeedPort에서 일어난다.
    """

    PENDING = "pending"
    READING = "reading"
    DONE = "done"


class Story(Entity):
    persona_id: UUID
    # 탈퇴해도 글은 남는다 — 그래서 nullable이고, 지워질 때 None이 된다.
    author_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    # 민감한 상담이 오가는 서비스라 익명이 기본이다(NFR-SEC-2).
    is_anonymous: bool = True
    relationship_stage: str | None = Field(default=None, max_length=50)
    nickname: str | None = Field(default=None, max_length=50)
    status: StoryStatus = StoryStatus.PENDING

    def display_author_id(self) -> UUID | None:
        """표시용 작성자. 익명이면 감춘다.

        저장은 원본 그대로 두고 **표시 단계에서만** 감춘다 — 신고·관리를 하려면
        누가 썼는지는 남아 있어야 하기 때문이다.
        """
        return None if self.is_anonymous else self.author_id


class Like(Entity):
    """스트리머(페르소나)에 대한 좋아요.

    (persona_id, user_id) 한 쌍당 하나만 존재한다 — 유일 제약으로 강제한다.
    좋아요/취소는 토글이 아니라 멱등 연산이라, 같은 요청이 두 번 와도 결과가 같다.
    """

    persona_id: UUID
    user_id: UUID
