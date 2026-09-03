"""wallet 리포지토리 포트의 SQLModel 구현.

여기가 이 슬라이스의 핵심이다 — 원장·잔액·후원의 원자성과 멱등성이 전부 이 파일에 있다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from aria.contexts.wallet.adapter.outbound.persistence.model import (
    CreditTransactionTable,
    DonationTable,
    WalletTable,
)
from aria.contexts.wallet.domain.model import (
    CreditTransaction,
    Donation,
    InsufficientCreditError,
    TransactionType,
)


def _entry_to_domain(row: CreditTransactionTable) -> CreditTransaction:
    return CreditTransaction(
        id=row.id,
        user_id=row.user_id,
        delta=row.delta,
        type=TransactionType(row.type),
        ref_id=row.ref_id,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
    )


def _donation_to_domain(row: DonationTable) -> Donation:
    return Donation(
        id=row.id,
        persona_id=row.persona_id,
        donor_id=row.donor_id,
        room_id=row.room_id,
        amount=row.amount,
        message=row.message,
    )


class SqlModelWalletRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def balance_of(self, user_id: UUID) -> int:
        row = self._session.get(WalletTable, user_id)
        # 지갑 로우는 첫 거래 때 생긴다 — 없다는 건 아직 아무 것도 없었다는 뜻이다.
        return row.credit_balance if row is not None else 0

    def apply(
        self, entry: CreditTransaction, *, donation: Donation | None = None
    ) -> int:
        # 0) 지갑 로우 확보. 본 트랜잭션 밖에서 미리 만들어 둔다 — 안에서 만들면 PK
        #    경합 시 rollback이 원장 insert까지 되돌려 버린다.
        self._ensure_wallet(entry.user_id)

        # 1) 원장 append. 멱등키 유일 제약이 중복 적용을 막는 **유일한** 관문이다.
        #    먼저 넣는 이유: 여기서 걸리면 잔액을 건드리기 전에 빠져나온다.
        self._session.add(
            CreditTransactionTable(
                id=entry.id,
                user_id=entry.user_id,
                delta=entry.delta,
                type=entry.type.value,
                ref_id=entry.ref_id,
                idempotency_key=entry.idempotency_key,
            )
        )
        try:
            self._session.flush()
        except IntegrityError:
            # 같은 멱등키가 이미 적용됐다. 원하는 최종 상태는 이미 달성돼 있다.
            self._session.rollback()
            return self.balance_of(entry.user_id)

        # 2) 잔액 갱신. 조건부 UPDATE 한 방이라 "읽고 → 판단하고 → 쓰는" 사이에
        #    다른 요청이 끼어들 틈이 없다.
        if entry.delta < 0 and not self._debit(entry.user_id, -entry.delta):
            self._session.rollback()  # 원장 insert도 함께 되돌아간다
            raise InsufficientCreditError("크레딧이 부족합니다")
        if entry.delta > 0:
            self._credit(entry.user_id, entry.delta)

        # 3) 후원 기록 — 차감과 같은 트랜잭션이라 둘이 갈라지지 않는다.
        if donation is not None:
            self._session.add(
                DonationTable(
                    id=donation.id,
                    persona_id=donation.persona_id,
                    donor_id=donation.donor_id,
                    room_id=donation.room_id,
                    amount=donation.amount,
                    message=donation.message,
                )
            )

        self._session.commit()
        return self.balance_of(entry.user_id)

    def list_entries(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> list[CreditTransaction]:
        rows = self._session.exec(
            select(CreditTransactionTable)
            .where(CreditTransactionTable.user_id == user_id)
            .order_by(col(CreditTransactionTable.created_at).desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [_entry_to_domain(row) for row in rows]

    def _ensure_wallet(self, user_id: UUID) -> None:
        if self._session.get(WalletTable, user_id) is not None:
            return
        self._session.add(WalletTable(user_id=user_id, credit_balance=0))
        try:
            self._session.commit()
        except IntegrityError:
            # 다른 요청이 먼저 만들었다. 원하는 상태(로우 존재)는 이미 달성됐다.
            self._session.rollback()

    def _credit(self, user_id: UUID, amount: int) -> None:
        self._session.execute(
            update(WalletTable)
            .where(col(WalletTable.user_id) == user_id)
            .values(credit_balance=col(WalletTable.credit_balance) + amount)
            .execution_options(synchronize_session=False)
        )

    def _debit(self, user_id: UUID, amount: int) -> bool:
        """잔액이 충분할 때만 차감. 성공 여부를 돌려준다.

        `credit_balance >= amount`를 WHERE에 넣는 것이 핵심이다 — 잔액 확인과 차감이
        한 문장이라 동시 요청 두 개가 같은 잔액을 보고 둘 다 통과하는 일이 없다.
        조건이 안 맞으면 UPDATE가 0행을 건드리고, 그것이 곧 '잔액 부족'이다.
        """
        result = self._session.execute(
            update(WalletTable)
            .where(
                col(WalletTable.user_id) == user_id,
                col(WalletTable.credit_balance) >= amount,
            )
            .values(credit_balance=col(WalletTable.credit_balance) - amount)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1


class SqlModelDonationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Donation]:
        rows = self._session.exec(
            select(DonationTable)
            .where(DonationTable.persona_id == persona_id)
            .order_by(col(DonationTable.created_at).desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [_donation_to_domain(row) for row in rows]
