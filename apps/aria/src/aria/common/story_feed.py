"""StoryFeedPort — 사연 낭독 소비의 컨텍스트 간 계약.

`chat`의 idle 진행이 방송국에 쌓인 사연을 하나씩 읽어 준다(FR-STATION-4 → FR-IDLE-2).
사연은 `community`가 소유하고, `chat`이 소비한다.

**왜 common에 있나.** 컨텍스트끼리는 서로 import하지 않으며, 경유는 common이다
(`docs/architecture.md`, `EventBusPort`와 같은 자리). 포트를 소비자(chat) 쪽에 두면
구현자(community)가 그것을 import해야 하는데, 독립성 계약은 **양방향**이라 그것도
금지된다. 그래서 계약이 양쪽 밖, 즉 커널에 산다. 배선은 합성 루트(`aria/app.py`)가
한다 — common은 컨텍스트를 import할 수 없으므로 여기서 조립할 수 없다.

**왜 이벤트가 아닌가.** 사연은 저볼륨이고 "다음 pending 하나를 집어 온다"는 claim
의미론이 이벤트 큐보다 자연스럽다. 근거는 `docs/events.md`.

**왜 community의 Story를 그대로 쓰지 않나.** 그러면 chat이 community의 도메인 타입을
알게 된다. 낭독에 필요한 것만 담은 DTO를 두고 어댑터가 변환한다 — `PersonaLLMPort`가
`Message`/`LLMResult`를 두고 OpenAI 타입과 매핑하는 것과 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PendingStory:
    """낭독 대기 사연 — chat이 읽는 데 필요한 것만."""

    story_id: UUID
    persona_id: UUID
    title: str
    content: str
    # 익명 사연에서 페르소나가 부를 이름. 없으면 호칭 없이 읽는다.
    nickname: str | None = None


class StoryFeedPort(Protocol):
    async def claim_next_pending(self, persona_id: UUID) -> PendingStory | None:
        """낭독할 사연 하나를 **선점**한다(pending → reading). 없으면 None.

        단순 조회가 아니라 상태 전이를 동반한다 — 인스턴스가 여럿이어도 같은 사연을
        두 번 읽지 않아야 하기 때문이다. 원자성은 구현이 보장한다.
        """
        ...

    async def mark_done(self, story_id: UUID) -> None:
        """낭독 완료(reading → done). 이미 done이면 아무 일도 하지 않는다."""
        ...

    async def release(self, story_id: UUID) -> None:
        """선점을 되돌린다(reading → pending). 낭독에 실패했을 때 부른다.

        **이게 없으면 claim한 사연이 영원히 `reading`에 갇힌다.** 생성이 실패하거나
        응답 슬롯을 못 잡으면 사연은 이미 큐에서 빠져나온 뒤인데 읽히지도 않은
        상태가 된다 — 시청자가 남긴 사연이 조용히 사라지는 셈이다.

        이미 `done`인 사연은 되돌리지 않는다. 읽고 나서 뒤늦게 도착한 실패 처리가
        끝난 사연을 대기열에 다시 넣으면 같은 사연을 두 번 읽는다.
        """
        ...
