"""wallet 도메인 — 크레딧 잔액·원장·후원.

레거시의 `UserWallet`/`CashLog`를 컨텍스트로 승격한 것이다(`docs/data-model.md`).
`user_id`·`persona_id`·`room_id`는 다른 컨텍스트의 엔티티를 가리키지만 **불투명 UUID**일
뿐이다 — wallet은 identity도 persona도 chat도 import하지 않는다.

**왜 잔액과 원장을 둘 다 두나.** 원장(`CreditTransaction`)이 진실이고 잔액은 그것의
materialized 값이다. 조회마다 `SUM(delta)`를 돌리면 원장이 커질수록 느려지고, 반대로
잔액만 두면 "왜 이 값이 됐는가"에 답할 수 없다(결제·후원이 얽히는 도메인에서 이건
치명적이다). 그래서 둘을 **한 트랜잭션에서 함께** 갱신한다 — 그 원자성이 아웃바운드
포트 `WalletRepository.apply()`가 한 번의 호출로 표현되는 이유다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aria.common.domain import Entity
from aria.common.errors import ConflictError


class TransactionType(Enum):
    """원장 항목의 성격. 부호까지 함께 규정한다.

    purchase: 결제 확정(payments → Kafka, Phase 4) · grant: 관리자 지급
    donation: 후원 사용 · refund: 결제 환불에 따른 크레딧 회수
    """

    PURCHASE = "purchase"
    GRANT = "grant"
    DONATION = "donation"
    REFUND = "refund"


# 부호는 타입이 결정한다 — "환불인데 잔액이 늘었다" 같은 원장을 애초에 못 만들게.
# refund는 결제 취소이므로 지급의 반대, 즉 회수(−)다(`docs/events.md`의
# `payments.credit-refunded`).
_CREDIT_TYPES = frozenset({TransactionType.PURCHASE, TransactionType.GRANT})
_DEBIT_TYPES = frozenset({TransactionType.DONATION, TransactionType.REFUND})


class InsufficientCreditError(ConflictError):
    """잔액보다 많이 쓰려 함. 409 — 재시도하면 달라질 수 있는 상태 충돌이다."""

    code = "insufficient_credit"


class Wallet(BaseModel):
    """한 사용자의 크레딧 잔액.

    `Entity`가 아니다 — 식별자가 대리키가 아니라 `user_id` 그 자체(1:1)이기 때문이다.
    대리키를 붙이면 "한 사용자에 지갑 둘"이 표현 가능해져 버린다.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    user_id: UUID
    # 음수 잔액은 도메인에서도 DB check 제약에서도 금지한다.
    credit_balance: int = Field(default=0, ge=0)


class CreditTransaction(Entity):
    """원장 한 줄. append-only — 한 번 쓰면 수정하지 않는다.

    `idempotency_key`는 **중복 적용을 막는 유일한 관문**이다. 유일 제약이 DB에 있어서,
    같은 지급 요청이 두 번 들어와도 두 번째 insert가 거부된다 — 앱 레벨의 "이미 있나?"
    검사로는 동시 요청 두 개를 막을 수 없다(community의 좋아요와 같은 방식).
    """

    user_id: UUID
    delta: int
    type: TransactionType
    # 무엇에 대한 항목인가(payment_id·donation_id). 컨텍스트를 넘나드는 값이라 문자열.
    ref_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=64)
    # 다른 엔티티와 달리 시각을 도메인에 둔다 — 원장에서 "언제"는 부가 정보가 아니라
    # 항목 자체의 일부다(정산·분쟁 대응). 저장된 값은 DB의 server_default가 이기고,
    # 여기 기본값은 아직 저장되지 않은 객체를 위한 것이다.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _sign_matches_type(self) -> CreditTransaction:
        if self.delta == 0:
            raise ValueError("delta는 0일 수 없습니다")
        if self.type in _CREDIT_TYPES and self.delta < 0:
            raise ValueError(f"{self.type.value}는 지급이므로 delta가 양수여야 합니다")
        if self.type in _DEBIT_TYPES and self.delta > 0:
            raise ValueError(f"{self.type.value}는 차감이므로 delta가 음수여야 합니다")
        return self


class Donation(Entity):
    """후원(슈퍼챗) 기록. 열혈순위(FR-STATION-6)의 소스다.

    크레딧 차감(`CreditTransaction`)과 **같은 트랜잭션**에 기록된다 — 둘이 갈라지면
    "돈은 빠졌는데 후원이 없다"거나 그 반대가 생긴다.
    """

    persona_id: UUID
    # 탈퇴해도 순위 기록은 남는다 — 그래서 nullable이다.
    donor_id: UUID | None = None
    room_id: UUID | None = None
    amount: int = Field(gt=0)
    message: str | None = Field(default=None, max_length=200)
