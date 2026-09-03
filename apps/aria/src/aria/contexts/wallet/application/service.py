"""wallet 유스케이스 — 크레딧 지급·사용·조회.

서비스는 트랜잭션을 열지 않는다. 원자성이 필요한 묶음(원장+잔액+후원)은 포트의
`apply()` 한 연산으로 표현돼 있고, 서비스는 **무엇을 적용할지**만 조립한다.
"""

from __future__ import annotations

from uuid import UUID

from aria.contexts.wallet.application.port.out.repository import (
    DonationRepository,
    WalletRepository,
)
from aria.contexts.wallet.domain.model import (
    CreditTransaction,
    Donation,
    TransactionType,
)

# 한 페이지 상한. community와 같은 값 — 페이징 정책을 컨텍스트마다 다르게 둘 이유가 없다.
MAX_PAGE_SIZE = 100


class WalletService:
    def __init__(self, wallets: WalletRepository) -> None:
        self._wallets = wallets

    def balance(self, user_id: UUID) -> int:
        return self._wallets.balance_of(user_id)

    def grant(
        self,
        user_id: UUID,
        credits: int,
        *,
        idempotency_key: str | None = None,
        ref_id: str | None = None,
        type: TransactionType = TransactionType.GRANT,
    ) -> int:
        """크레딧을 지급하고 적용 후 잔액을 돌려준다.

        지금 호출자는 관리자 엔드포인트뿐이지만, payments의 결제 확정 이벤트도
        `type=PURCHASE`로 같은 경로를 타게 된다(Phase 4) — 지급 경로를 하나로 둔다.

        `credits`가 0 이하면 도메인 검증(`CreditTransaction`)이 막는다.
        """
        entry = CreditTransaction(
            user_id=user_id,
            delta=credits,
            type=type,
            ref_id=ref_id,
            idempotency_key=idempotency_key,
        )
        return self._wallets.apply(entry)

    def history(
        self, user_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[CreditTransaction]:
        return self._wallets.list_entries(
            user_id, limit=min(limit, MAX_PAGE_SIZE), offset=max(offset, 0)
        )


class DonationService:
    """후원(슈퍼챗).

    chat이 이 유스케이스를 직접 부르지는 않는다 — 컨텍스트끼리는 서로 import하지
    않으므로 common의 포트를 거친다(C-2).
    """

    def __init__(
        self, wallets: WalletRepository, donations: DonationRepository
    ) -> None:
        self._wallets = wallets
        self._donations = donations

    def donate(
        self,
        donor_id: UUID,
        persona_id: UUID,
        amount: int,
        *,
        message: str | None = None,
        room_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Donation:
        """크레딧을 차감하고 후원을 기록한다. 잔액이 모자라면 아무 것도 남지 않는다.

        `ref_id`로 원장과 후원을 잇는다 — 나중에 "이 차감이 어느 후원이었나"를
        원장만 보고 답할 수 있어야 하기 때문이다.
        """
        donation = Donation(
            persona_id=persona_id,
            donor_id=donor_id,
            room_id=room_id,
            amount=amount,
            message=message,
        )
        entry = CreditTransaction(
            user_id=donor_id,
            delta=-amount,
            type=TransactionType.DONATION,
            ref_id=str(donation.id),
            idempotency_key=idempotency_key,
        )
        self._wallets.apply(entry, donation=donation)
        return donation

    def list_for_persona(
        self, persona_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[Donation]:
        return self._donations.list_by_persona(
            persona_id, limit=min(limit, MAX_PAGE_SIZE), offset=max(offset, 0)
        )
