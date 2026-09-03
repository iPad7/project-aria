"""wallet HTTP 라우터 — 내 잔액·내 원장, 그리고 관리자 지급.

**후원 엔드포인트는 여기 없다.** 후원은 방송 중 슈퍼챗으로 들어오므로 진입점이
chat의 WebSocket이고, chat이 common의 포트를 거쳐 이 컨텍스트를 부른다(C-2).

**잔액 조회는 늘 '나'다.** 남의 지갑을 볼 수 있는 경로를 만들지 않는다 — 경로 파라미터로
user_id를 받는 순간 소유권 검사를 빠뜨릴 자리가 생긴다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from aria.common.auth import Principal, get_current_principal, require_staff
from aria.contexts.wallet.adapter.inbound.http.deps import get_wallet_service
from aria.contexts.wallet.adapter.inbound.http.schema import (
    BalanceResponse,
    GrantRequest,
    TransactionResponse,
)
from aria.contexts.wallet.application.service import MAX_PAGE_SIZE, WalletService

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/me", response_model=BalanceResponse)
def my_balance(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WalletService, Depends(get_wallet_service)],
) -> BalanceResponse:
    return BalanceResponse(
        user_id=principal.user_id,
        credit_balance=service.balance(principal.user_id),
    )


@router.get("/me/transactions", response_model=list[TransactionResponse])
def my_transactions(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WalletService, Depends(get_wallet_service)],
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> list[TransactionResponse]:
    entries = service.history(principal.user_id, limit=limit, offset=offset)
    return [
        TransactionResponse(
            id=e.id,
            delta=e.delta,
            type=e.type.value,
            ref_id=e.ref_id,
            created_at=e.created_at,
        )
        for e in entries
    ]


@router.post("/grants", response_model=BalanceResponse)
def grant_credits(
    body: GrantRequest,
    _staff: Annotated[Principal, Depends(require_staff)],
    service: Annotated[WalletService, Depends(get_wallet_service)],
) -> BalanceResponse:
    """관리자 크레딧 지급.

    결제(Phase 4)가 붙기 전까지 크레딧의 유일한 공급원이자, 붙은 뒤에도 보상·정정에
    쓰인다. 201이 아니라 200을 쓴다 — 멱등키가 같으면 새로 만들지 않고 현재 상태를
    돌려주므로 "생성됨"이 늘 참은 아니다.
    """
    balance = service.grant(
        body.user_id,
        body.credits,
        idempotency_key=body.idempotency_key,
        ref_id=body.ref_id,
    )
    return BalanceResponse(user_id=body.user_id, credit_balance=balance)
