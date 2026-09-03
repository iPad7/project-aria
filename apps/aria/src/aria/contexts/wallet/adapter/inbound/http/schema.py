"""wallet HTTP 요청/응답 DTO."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from aria.common.schema import SchemaBase


class BalanceResponse(SchemaBase):
    user_id: UUID
    credit_balance: int


class GrantRequest(SchemaBase):
    """관리자 크레딧 지급.

    `idempotency_key`는 필수다 — 관리자 콘솔의 더블클릭이나 네트워크 재시도가
    두 번 지급되면 안 되기 때문이다. 선택으로 두면 아무도 안 보낸다.
    """

    user_id: UUID
    credits: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=64)
    # 지급 근거(티켓 번호 등). 나중에 원장만 보고 이유를 되짚을 수 있게.
    ref_id: str | None = Field(default=None, max_length=64)


class TransactionResponse(SchemaBase):
    id: UUID
    delta: int
    type: str
    ref_id: str | None
    created_at: datetime
