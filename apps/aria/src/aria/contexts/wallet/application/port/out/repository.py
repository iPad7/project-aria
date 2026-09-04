"""아웃바운드 포트: 크레딧 영속성."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aria.contexts.wallet.domain.model import CreditTransaction, Donation, DonorTotal


class WalletRepository(Protocol):
    """잔액 + 원장. 둘은 늘 함께 움직이므로 한 포트가 소유한다."""

    def balance_of(self, user_id: UUID) -> int:
        """현재 잔액. 지갑이 아직 없으면 0."""
        ...

    def apply(
        self, entry: CreditTransaction, *, donation: Donation | None = None
    ) -> int:
        """원장 append + 잔액 갱신(+ 있으면 후원 기록)을 **한 트랜잭션에** 적용한다.

        왜 한 호출인가: 이 셋이 갈라지면 원장과 잔액이 어긋난다. 트랜잭션 경계를
        서비스가 쥘 수 없으므로(서비스는 Session을 모른다) 원자적 단위 자체를
        포트 연산으로 표현했다.

        - `entry.idempotency_key`가 이미 쓰였으면 **아무 것도 하지 않고** 현재 잔액을
          돌려준다(멱등). 재시도가 두 번 지급되면 안 되기 때문이다.
        - 차감인데 잔액이 모자라면 `InsufficientCreditError`. 잔액 확인과 차감이
          한 조건부 UPDATE여야 동시 요청 두 개가 같이 통과하지 못한다.

        반환값은 적용 후 잔액.
        """
        ...

    def list_entries(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> list[CreditTransaction]:
        """한 사용자의 원장을 최신순으로."""
        ...


class DonationRepository(Protocol):
    """후원 조회. 쓰기는 `WalletRepository.apply()`가 차감과 함께 한다."""

    def list_by_persona(
        self, persona_id: UUID, *, limit: int, offset: int
    ) -> list[Donation]: ...

    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorTotal]:
        """한 페르소나의 후원자를 누적 금액 내림차순으로(열혈순위, FR-STATION-6).

        `donor_id`가 없는 후원은 제외한다 — 서로 다른 사람의 익명 후원을 한 줄로
        합치면 실재하지 않는 1위가 만들어진다.
        """
        ...
