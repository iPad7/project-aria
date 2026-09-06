"""진행 유스케이스 — 페르소나가 **지금 무슨 말을 할지** 한 곳에서 정한다.

셋 중 하나다: 선별된 댓글에 답하거나(FR-GEN-1·2), 대기 중인 사연을 읽거나
(FR-IDLE-2·3), 스스로 말을 꺼내거나(FR-IDLE-1).

**한 서비스인 이유: 셋은 경쟁 관계다.** 한 방에서 동시에 하나의 응답만 만들 수
있으므로 어차피 한 곳에서 골라야 한다. 나누면 둘이 각자 발행하고 코디네이터가
하나를 버리는 낭비가 생긴다 — 선별을 도입하며 없애려는 바로 그 문제다.

**듣는 사람이 없으면 스스로 말을 꺼내지 않는다.** 댓글에 답하는 것은 청한 사람이
있으므로 그대로 하지만, 사연 낭독과 자율발화는 시청자가 0명이면 건너뛴다.

**우선순위: 댓글 > 사연 > 자율발화.** 시청자가 지금 말을 걸고 있으면 그게 먼저이고,
조용하면 남겨 둔 사연을, 그것도 없으면 혼잣말을 한다. `ChatSource` 의 우선순위 값
(CHAT=2 > STORY=IDLE=1)과 같은 순서라 값은 바꾸지 않는다 — 여기서 하나만 고르므로
값으로 다툴 일이 없다.

**생성을 직접 하지 않는다.** C-4-1에서 생성이 워커로 빠졌으므로 여기도 요청만
발행한다 — 슬롯을 잡고 만들고 발행하는 것은 generation-worker의 일이다. 그래서 이
서비스는 LLM도 코디네이터도 모른다.

**락을 사연보다 먼저 잡는 이유**는 `port/out/idle_lock.py`에 적혀 있다: claim이
사연을 진짜로 소비해 버리므로, 코디네이터가 나중에 걸러 주는 것으로는 늦다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from aria.common.story_feed import PendingStory, StoryFeedPort
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.audience import RoomAudience
from aria.contexts.chat.application.port.out.candidates import CandidateBuffer
from aria.contexts.chat.application.port.out.clustering import TopicClusterer
from aria.contexts.chat.application.port.out.coordinator import ResponseCoordinator
from aria.contexts.chat.application.port.out.idle_lock import IdleLock
from aria.contexts.chat.domain.source import ChatSource
from aria.contexts.chat.domain.topic import Scored, select_best

logger = logging.getLogger(__name__)


def _story_prompt(story: PendingStory) -> str:
    """사연 낭독을 유도하는 입력.

    페르소나별 말투 해석은 #22의 몫이라 여기서는 사실만 전달한다 — 누가 어떤 사연을
    남겼는지. `_thanks_prompt`(후원)와 같은 방침이다.
    """
    who = f"{story.nickname}님이" if story.nickname else "한 시청자가"
    return (
        f"{who} 이런 사연을 보냈습니다.\n"
        f"제목: {story.title}\n"
        f"내용: {story.content}\n"
        "읽어 주고 공감과 조언을 건네세요."
    )


# 자율발화(FR-IDLE-1)의 입력. 사연이 없을 때 빈자리를 메운다.
_SELF_TALK_PROMPT = (
    "지금 채팅이 조용합니다. 시청자에게 먼저 말을 걸어 주세요 — "
    "혼잣말이나 가볍게 답할 수 있는 질문이면 좋습니다."
)


@dataclass(frozen=True)
class Progress:
    """무엇을 발행했는지. 트레이스·로그에 실린다."""

    source: ChatSource
    prompt: str
    # 선별했을 때만. "왜 저 댓글을 골랐나"의 근거다.
    selection: Scored | None = None
    candidate_count: int = 0
    topic_count: int = 0


class ProgressService:
    def __init__(
        self,
        activity: ActivityTracker,
        lock: IdleLock,
        stories: StoryFeedPort,
        generation: GenerationRequestPublisher,
        candidates: CandidateBuffer,
        clusterer: TopicClusterer,
        coordinator: ResponseCoordinator,
        audience: RoomAudience,
        *,
        threshold_seconds: float,
    ) -> None:
        self._activity = activity
        self._lock = lock
        self._stories = stories
        self._generation = generation
        self._candidates = candidates
        self._clusterer = clusterer
        self._coordinator = coordinator
        self._audience = audience
        self._threshold = threshold_seconds

    async def advance(self, room_id: UUID, persona_id: UUID) -> Progress | None:
        """방 하나를 살펴보고 필요하면 진행시킨다. 발행했으면 `Progress`.

        **락이 가장 앞이다.** 후보를 꺼내는 것도 사연 claim도 **소비**라 되돌릴 수
        없다 — 코디네이터가 나중에 걸러 주는 것으로는 늦다.
        """
        # **소비하기 전에 두 문을 지난다.**
        #
        # ① 다른 워커가 이 방을 맡았는가(락) ② 지금 누가 응답을 만들고 있는가(슬롯).
        # 후보를 꺼내는 것도 사연 claim 도 되돌릴 수 없는 소비라, 꺼내 놓고 생성
        # 워커가 슬롯을 못 잡으면 그 배치의 댓글이 답도 없이 사라진다.
        #
        # 슬롯은 **묻기만 하고 잡지 않는다** — 잡는 것은 생성 워커의 일이다. 그래서
        # 확인과 생성 사이에 선점당할 수는 있지만, 그건 더 높은 우선순위가 대신
        # 답했다는 뜻이라 문제가 아니다.
        if await self._coordinator.is_busy(room_id):
            return None
        if not await self._lock.acquire(room_id):
            return None

        story: PendingStory | None = None
        try:
            plan = await self._plan(room_id, persona_id)
            if plan is None:
                return None
            story = plan.story
            await self._generation.request(
                room_id,
                persona_id,
                plan.progress.source,
                plan.progress.prompt,
                _selection_metadata(plan.progress),
            )
            if story is not None:
                # **`done`은 지금 "발행됐다"는 뜻이다.** 진짜 낭독 완료를 아는 것은
                # 응답을 내보낸 generation-worker인데, 워커는 사연도 DB도 모른다
                # (C-4의 경계). 완료 신호가 생기는 시점은 미디어 송출이 붙어
                # "다 읽었다"가 정의될 때다.
                #
                # 그때까지 여기서 표시하는 이유: 안 하면 읽은 사연이 전부 `reading`에
                # 남아 게시판이 영원히 "낭독 중"으로 보인다. 워커가 실패하면(DLQ)
                # 읽히지 않은 사연이 done이 되는 손실이 있지만, 그쪽이 더 드물다.
                await self._stories.mark_done(story.story_id)
        except Exception:
            # 발행에 실패했는데 사연을 claim해 둔 상태면 그 사연은 `reading`에 갇힌다.
            # 되돌려 다음 기회에 읽히게 한다.
            if story is not None:
                await self._stories.release(story.story_id)
            logger.exception("진행 실패 room_id=%s", room_id)
            raise
        finally:
            await self._lock.release(room_id)

        # 진행했다고 표시한다. 이게 없으면 생성이 끝나기 전에 다음 틱이 또 이 방을
        # 집는다(조용한 방은 idle 판정으로, 채팅 있는 방은 남은 후보로).
        await self._activity.touch(room_id)
        return plan.progress

    async def _plan(self, room_id: UUID, persona_id: UUID) -> _Plan | None:
        """무엇을 말할지 고른다. 말할 게 없으면 None.

        **댓글이 먼저다.** 시청자가 지금 말을 걸고 있는데 혼잣말을 하면 이상하다.
        """
        candidates = await self._candidates.take_all(room_id)
        if candidates:
            topics = self._clusterer.cluster(candidates)
            best = select_best(topics)
            if best is not None:
                return _Plan(
                    progress=Progress(
                        source=ChatSource.CHAT,
                        prompt=best.candidate.text,
                        selection=best,
                        candidate_count=len(candidates),
                        topic_count=len(topics),
                    )
                )

        # **여기부터는 아무도 청하지 않은 발화다.** 듣는 사람이 없으면 하지 않는다.
        #
        # 자율발화는 비용만 나가지만 사연은 더 나쁘다 — 낭독은 시청자가 남긴 사연을
        # `done`으로 소비하므로, 빈 방에서 읽으면 그 사연은 아무에게도 닿지 못한 채
        # 사라진다. 반면 위의 댓글은 남긴 사람이 있으므로 지금 보고 있지 않더라도
        # 답한다(재접속하면 방 채널로 받는다).
        if await self._audience.viewer_count(room_id) == 0:
            return None

        # 채팅이 없으면 조용한 방인지 본다. 방금 답했다면 아직 idle이 아니다.
        if not await self._activity.is_idle(room_id, self._threshold):
            return None

        story = await self._stories.claim_next_pending(persona_id)
        if story is not None:
            return _Plan(
                progress=Progress(source=ChatSource.STORY, prompt=_story_prompt(story)),
                story=story,
            )
        return _Plan(
            progress=Progress(source=ChatSource.IDLE, prompt=_SELF_TALK_PROMPT)
        )


@dataclass(frozen=True)
class _Plan:
    progress: Progress
    story: PendingStory | None = None


def _selection_metadata(progress: Progress) -> dict[str, object] | None:
    """선별 근거를 트레이스 속성으로. 선별하지 않았으면 None.

    관측성(#61)을 선별보다 먼저 당긴 이유가 이것이다 — 이게 없으면 "왜 저 댓글을
    골랐나"를 로그로 헤매야 한다.
    """
    if progress.selection is None:
        return None
    return {
        "candidates": progress.candidate_count,
        "topics": progress.topic_count,
        "selected_score": progress.selection.score,
        "selected_reasons": list(progress.selection.reasons),
    }
