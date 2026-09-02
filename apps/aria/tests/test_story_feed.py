"""StoryFeedPort — 컨텍스트 간 포트 배선 테스트.

소비자(chat idle, FR-IDLE-2)는 아직 없다. 이 테스트가 **소비자 역할**을 한다 —
common의 계약만 알고, community의 구현을 주입받아 쓴다. chat이 나중에 할 일을
그대로 흉내내므로, 배선이 성립하는지가 여기서 검증된다.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from aria.common.story_feed import PendingStory, StoryFeedPort
from aria.contexts.community.adapter.outbound.persistence.repository import (
    SqlModelStoryRepository,
)
from aria.contexts.community.adapter.outbound.story_feed import CommunityStoryFeed
from aria.contexts.community.application.service import StoryService


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def feed(session: Session) -> StoryFeedPort:
    # 합성 루트가 할 배선을 여기서 한다.
    return CommunityStoryFeed(SqlModelStoryRepository(session))


@pytest.fixture
def stories(session: Session) -> StoryService:
    return StoryService(SqlModelStoryRepository(session))


def _submit(stories: StoryService, persona_id, title: str, **over: object):
    return stories.submit(
        persona_id=persona_id,
        author_id=uuid4(),
        title=title,
        content=f"{title} 내용",
        **over,  # type: ignore[arg-type]
    )


async def test_claim_returns_pending_story(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    persona_id = uuid4()
    _submit(stories, persona_id, "첫 사연", nickname="고민이")

    claimed = await feed.claim_next_pending(persona_id)

    assert claimed is not None
    assert claimed.title == "첫 사연"
    assert claimed.nickname == "고민이"  # 페르소나가 부를 이름
    assert claimed.persona_id == persona_id


async def test_claim_returns_none_when_empty(feed: StoryFeedPort) -> None:
    assert await feed.claim_next_pending(uuid4()) is None


async def test_claim_is_scoped_to_persona(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    mine, other = uuid4(), uuid4()
    _submit(stories, other, "남의 방송국 사연")

    assert await feed.claim_next_pending(mine) is None


async def test_claim_takes_oldest_first(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    # 게시판 목록은 최신순이지만 낭독은 오래 기다린 사연부터다.
    persona_id = uuid4()
    _submit(stories, persona_id, "먼저 온 사연")
    _submit(stories, persona_id, "나중 사연")

    claimed = await feed.claim_next_pending(persona_id)

    assert claimed is not None
    assert claimed.title == "먼저 온 사연"


async def test_claim_does_not_hand_out_same_story_twice(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    # 선점이라 두 번째 호출은 같은 사연을 주면 안 된다 — 인스턴스가 여럿일 때
    # 같은 사연을 두 번 읽지 않기 위한 핵심 성질.
    persona_id = uuid4()
    _submit(stories, persona_id, "하나뿐인 사연")

    first = await feed.claim_next_pending(persona_id)
    second = await feed.claim_next_pending(persona_id)

    assert first is not None
    assert second is None


async def test_mark_done_is_idempotent(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    persona_id = uuid4()
    _submit(stories, persona_id, "낭독할 사연")
    claimed = await feed.claim_next_pending(persona_id)
    assert claimed is not None

    await feed.mark_done(claimed.story_id)
    await feed.mark_done(claimed.story_id)  # 두 번 불러도 안전

    assert stories.get(claimed.story_id).status.value == "done"


async def test_mark_done_on_missing_story_is_noop(feed: StoryFeedPort) -> None:
    await feed.mark_done(uuid4())  # 예외 없이 지나간다


async def test_claimed_story_leaves_the_board_queue(
    feed: StoryFeedPort, stories: StoryService
) -> None:
    # 낭독 중/완료된 사연도 게시판에는 그대로 보인다 — 상태는 낭독 진행이지
    # 공개 여부가 아니다.
    persona_id = uuid4()
    _submit(stories, persona_id, "사연")
    claimed = await feed.claim_next_pending(persona_id)
    assert claimed is not None

    listed = stories.list_for_persona(persona_id)

    assert len(listed) == 1
    assert listed[0].status.value == "reading"


def test_port_contract_is_structural() -> None:
    """common의 계약만으로 구현을 받아들일 수 있는가.

    chat은 CommunityStoryFeed를 import하지 않고 StoryFeedPort 타입으로만 다룬다.
    이 검사가 통과한다는 것은 배선이 계약만으로 성립한다는 뜻이다.
    """
    assert isinstance(PendingStory(uuid4(), uuid4(), "t", "c"), PendingStory)
    assert hasattr(CommunityStoryFeed, "claim_next_pending")
    assert hasattr(CommunityStoryFeed, "mark_done")
