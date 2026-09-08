"""payments가 보낸 크레딧 사실을 받아 원장에 적용한다.

**payments를 모른다.** 아는 것은 `docs/events.md`의 페이로드 계약뿐이고, 그 계약은
토픽 문자열과 필드 이름으로만 존재한다 — 두 서비스가 코드를 공유하지 않는다는 것이
서비스를 나눈 이유의 절반이다(나머지 절반은 결제 시크릿 격리, NFR-SEC-1).

**멱등이 여기의 전부다.** relay는 at-least-once라 같은 확정이 두 번 올 수 있고, 그건
버그가 아니라 outbox가 유실을 중복으로 바꾼 결과다. 중복이 이중 지급이 되지 않게 하는
것은 `idempotency_key`의 원장 유일 제약 하나뿐이다 — 여기서 "이미 봤나"를 따로 세지
않는 이유이기도 하다. 세는 곳이 둘이면 둘이 어긋난다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from aria.contexts.wallet.application.service import WalletService
from aria.contexts.wallet.domain.model import TransactionType

logger = logging.getLogger(__name__)

CREDIT_PURCHASE_CONFIRMED = "payments.credit-purchase-confirmed"
CREDIT_REFUNDED = "payments.credit-refunded"

# 우리가 읽을 줄 아는 페이로드 버전. 모르는 버전은 추측하지 않고 DLQ로 보낸다(C-4-2) —
# 필드가 바뀐 메시지를 옛 코드가 반쯤 읽어 **엉뚱한 금액을 지급하는** 것이 최악이다.
SUPPORTED_VERSION = 1


@dataclass(frozen=True)
class CreditEvent:
    """지급이든 회수든 같은 모양이다 — 부호만 다르다."""

    payment_id: UUID
    user_id: UUID
    credits: int
    idempotency_key: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CreditEvent:
        """계약대로 읽는다. 어긋나면 던진다 — 어댑터가 받아 DLQ로 보낸다.

        `v`가 없으면 1로 본다. C-4-2 이전에 발행돼 큐에 남아 있던 것을 위한 것이고,
        payments는 처음부터 실어 보낸다.
        """
        version = payload.get("v", 1)
        if version != SUPPORTED_VERSION:
            raise ValueError(f"모르는 페이로드 버전: {version}")

        credits = int(payload["credits"])
        if credits <= 0:
            # 0이나 음수를 그대로 태우면 도메인 검증에 걸려 DLQ로 가지만, 여기서
            # 걸러야 무엇이 잘못됐는지가 로그에 남는다.
            raise ValueError(f"크레딧이 양수가 아니다: {credits}")

        return cls(
            payment_id=UUID(payload["payment_id"]),
            user_id=UUID(payload["user_id"]),
            credits=credits,
            idempotency_key=str(payload["idempotency_key"]),
        )


class CreditEventService:
    """결제 확정 → 지급, 환불 → 회수.

    지급 경로를 새로 만들지 않고 `WalletService.grant()`를 탄다. C-1에서 그 메서드에
    "결제 확정 이벤트도 `type=PURCHASE`로 같은 경로를 타게 된다(Phase 4)"고 적어 둔
    자리이며, 지급이 두 갈래로 갈라지면 잔액이 왜 그 값인지를 두 곳에서 봐야 한다.
    """

    def __init__(self, wallets: WalletService) -> None:
        self._wallets = wallets

    def purchase_confirmed(self, event: CreditEvent) -> int:
        balance = self._wallets.grant(
            event.user_id,
            event.credits,
            idempotency_key=event.idempotency_key,
            ref_id=str(event.payment_id),
            type=TransactionType.PURCHASE,
        )
        logger.info(
            "크레딧 지급 payment_id=%s user_id=%s +%d 잔액=%d",
            event.payment_id,
            event.user_id,
            event.credits,
            balance,
        )
        return balance

    def refunded(self, event: CreditEvent) -> int:
        """환불된 만큼 되돌린다.

        **잔액이 모자라면 실패한다** — 이미 써 버린 크레딧을 환불하려는 경우다.
        음수 잔액으로 두지 않고 `InsufficientCreditError`를 올려 DLQ로 보낸다: 돈이
        걸린 일이라 사람이 보고 판단해야 한다(환불 취소든 별도 정산이든).
        """
        balance = self._wallets.grant(
            event.user_id,
            -event.credits,
            idempotency_key=event.idempotency_key,
            ref_id=str(event.payment_id),
            type=TransactionType.REFUND,
        )
        logger.info(
            "크레딧 회수 payment_id=%s user_id=%s -%d 잔액=%d",
            event.payment_id,
            event.user_id,
            event.credits,
            balance,
        )
        return balance
