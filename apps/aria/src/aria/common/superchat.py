"""SuperchatPort — 후원 결제의 컨텍스트 간 계약.

시청자가 방송 중 크레딧으로 후원하면(FR-PAY-3), 크레딧 차감과 후원 기록은 `wallet`이
하고 그 트리거와 감사 응답(FR-GEN-6)은 `chat`이 한다. 두 컨텍스트가 서로를 import하지
않으므로 계약이 커널에 산다 — `StoryFeedPort`와 같은 자리, 같은 이유다.

**왜 이벤트가 아니라 동기 포트인가.** 사연 낭독과 달리 후원은 *실패를 즉시 알아야
한다*. 차감이 실패했는데 감사 응답이 나가면 공짜 후원이 되고, 반대로 차감만 되고
아무 표시도 안 나가면 돈만 사라진다. 비동기로 흘려보내면 그 사이를 메울 방법이 없다.
`docs/events.md`가 payments↔wallet만 이벤트로 규정하고 chat↔wallet은 비워 둔 자리다.

**실패는 `InsufficientCreditError`다.** wallet이 던지고 chat이 잡는다 — 그래서 예외도
컨텍스트가 아니라 `common.errors`에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class SuperchatReceipt:
    """차감 결과 — chat이 방송에 표시하는 데 필요한 것만.

    wallet의 `Donation`을 그대로 넘기지 않는다. 그러면 chat이 wallet의 도메인 타입을
    알게 된다(`PendingStory`와 같은 이유).
    """

    donation_id: UUID
    balance_after: int


class SuperchatPort(Protocol):
    async def charge(
        self,
        donor_id: UUID,
        persona_id: UUID,
        amount: int,
        *,
        room_id: UUID | None = None,
        message: str | None = None,
        idempotency_key: str | None = None,
    ) -> SuperchatReceipt:
        """크레딧을 차감하고 후원을 기록한다. 둘은 한 트랜잭션이다.

        잔액이 모자라면 `InsufficientCreditError`를 던지고 **아무 것도 남기지 않는다**.
        `idempotency_key`를 주면 같은 키의 재시도가 두 번 과금되지 않는다.
        """
        ...
