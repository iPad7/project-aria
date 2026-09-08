"""영속 포트.

**원자성이 필요한 묶음은 포트의 한 연산으로 표현한다.** 서비스는 트랜잭션을 열지
않는다 — aria의 `WalletRepository.apply()`와 같은 방침이다. outbox에서 이게 특히
중요한데, "상태 변경과 outbox 로우가 같은 트랜잭션"이라는 것이 이 패턴의 **전부**라
그 불변식이 서비스 코드의 호출 순서에 달려 있으면 안 되기 때문이다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from payments.domain.model import OutboxMessage, Payment


class PaymentRepository(Protocol):
    def add(self, payment: Payment) -> None: ...

    def get_by_order_id(self, order_id: str) -> Payment | None: ...

    def save_with_outbox(self, payment: Payment, message: OutboxMessage) -> None:
        """결제 상태와 outbox 로우를 **한 트랜잭션에** 쓴다.

        둘 중 하나만 남는 경우가 없어야 한다. 그것이 이 메서드가 존재하는 이유이고,
        `save()`와 `enqueue()`를 따로 두지 않은 이유다.
        """
        ...


class OutboxRepository(Protocol):
    def fetch_unpublished(self, limit: int) -> list[OutboxMessage]:
        """아직 안 나간 것을 기록된 순서대로 준다.

        PK가 UUIDv7이라 `id` 오름차순이 곧 기록 순서다 — 별도 정렬 컬럼이 필요 없다.
        """
        ...

    def mark_published(self, message_id: UUID) -> None: ...
