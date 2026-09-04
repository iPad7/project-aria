"""idle 진행 유스케이스 — 방송이 비어 있지 않게 한다.

무채팅이 지속되면 페르소나가 스스로 말을 꺼내거나(FR-IDLE-1), 대기 중인 사연을
읽는다(FR-IDLE-2·3). 레거시의 idle 타이머를 옮긴 것이다.

**사연이 먼저다.** 사연은 시청자가 실제로 남긴 것이고 자율발화는 빈자리를 메우는
것이다. `ChatSource.STORY`와 `IDLE`의 우선순위 값은 둘 다 1로 두는데, 여기서 하나만
고르므로 값으로 다툴 일이 없기 때문이다.

**생성을 직접 하지 않는다.** C-4-1에서 생성이 워커로 빠졌으므로 여기도 요청만
발행한다 — 슬롯을 잡고 만들고 발행하는 것은 generation-worker의 일이다. 그래서 이
서비스는 LLM도 코디네이터도 모른다.

**락을 사연보다 먼저 잡는 이유**는 `port/out/idle_lock.py`에 적혀 있다: claim이
사연을 진짜로 소비해 버리므로, 코디네이터가 나중에 걸러 주는 것으로는 늦다.
"""

from __future__ import annotations

import logging
from uuid import UUID

from aria.common.story_feed import PendingStory, StoryFeedPort
from aria.contexts.chat.application.generation import GenerationRequestPublisher
from aria.contexts.chat.application.port.out.activity import ActivityTracker
from aria.contexts.chat.application.port.out.idle_lock import IdleLock
from aria.contexts.chat.domain.source import ChatSource

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


class IdleProgressService:
    def __init__(
        self,
        activity: ActivityTracker,
        lock: IdleLock,
        stories: StoryFeedPort,
        generation: GenerationRequestPublisher,
        *,
        threshold_seconds: float,
    ) -> None:
        self._activity = activity
        self._lock = lock
        self._stories = stories
        self._generation = generation
        self._threshold = threshold_seconds

    async def advance(self, room_id: UUID, persona_id: UUID) -> bool:
        """방 하나를 살펴보고 필요하면 진행시킨다. 발행했으면 True.

        순서가 중요하다: ① idle인가 → ② 락 → ③ 사연 claim → ④ 발행. ②가 ③보다
        앞이어야 claim이 헛되이 소비되지 않는다.
        """
        if not await self._activity.is_idle(room_id, self._threshold):
            return False
        if not await self._lock.acquire(room_id):
            # 다른 워커가 이 방을 맡았다. 사연을 건드리기 전에 물러난다.
            return False

        story: PendingStory | None = None
        try:
            story = await self._stories.claim_next_pending(persona_id)
            source, prompt = (
                (ChatSource.STORY, _story_prompt(story))
                if story is not None
                else (ChatSource.IDLE, _SELF_TALK_PROMPT)
            )
            await self._generation.request(room_id, persona_id, source, prompt)
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
            logger.exception("idle 진행 실패 room_id=%s", room_id)
            raise
        finally:
            await self._lock.release(room_id)

        # 진행했다고 표시한다. 이게 없으면 생성이 끝나기 전에 다음 틱이 또 idle로
        # 보고 같은 방을 다시 집는다.
        await self._activity.touch(room_id)
        return True
