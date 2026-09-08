"""결제 HTTP 경계 — 준비·확정·환불·조회.

**크레딧은 여기서 주지 않는다.** 확정이 하는 일은 결제 상태를 바꾸고 outbox에 사실을
남기는 것까지이고, 실제 지급은 relay가 발행한 이벤트를 aria의 wallet-workers가 받아서
한다. 그래서 이 응답이 200이어도 잔액은 아직 그대로일 수 있다 — 폴링 주기만큼(기본
1초) 늦다. 결제 화면은 그 사이를 "처리 중"으로 보여 주면 된다.

**인증이 아직 없다.** 이 엔드포인트들은 게이트웨이 콜백과 내부 호출을 상정한 것이고,
공개 배포 전에 게이트웨이 서명 검증(Toss webhook)과 내부 호출 인증이 필요하다.
그건 Toss 실연동과 함께 오는 일이라 이번 범위 밖이다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from payments.adapter.inbound.http.schema import (
    ConfirmRequest,
    PaymentResponse,
    PrepareRequest,
    RefundRequest,
)
from payments.adapter.outbound.gateway.factory import build_gateway
from payments.adapter.outbound.persistence.repository import SqlModelPaymentRepository
from payments.application.service import (
    AmountMismatchError,
    PaymentNotFoundError,
    PaymentService,
)
from payments.common.db import get_session
from payments.domain.model import PaymentError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


def get_service(session: Session = Depends(get_session)) -> PaymentService:
    return PaymentService(SqlModelPaymentRepository(session), build_gateway())


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PaymentResponse)
def prepare(
    body: PrepareRequest, service: PaymentService = Depends(get_service)
) -> PaymentResponse:
    return PaymentResponse.of(
        service.prepare(body.user_id, body.order_id, body.amount, body.credits)
    )


@router.post("/confirm", response_model=PaymentResponse)
async def confirm(
    body: ConfirmRequest, service: PaymentService = Depends(get_service)
) -> PaymentResponse:
    try:
        payment = await service.confirm(body.order_id, body.provider_key)
    except PaymentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AmountMismatchError as exc:
        # 승인 금액이 우리 기록과 다르다. 사람이 봐야 하는 상황이라 로그에 남긴다.
        logger.error("금액 불일치 order_id=%s: %s", body.order_id, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PaymentResponse.of(payment)


@router.post("/{order_id}/refund", response_model=PaymentResponse)
async def refund(
    order_id: str,
    body: RefundRequest,
    service: PaymentService = Depends(get_service),
) -> PaymentResponse:
    try:
        payment = await service.refund(order_id, body.reason)
    except PaymentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PaymentResponse.of(payment)


@router.get("/{order_id}", response_model=PaymentResponse)
def get(
    order_id: str, service: PaymentService = Depends(get_service)
) -> PaymentResponse:
    try:
        return PaymentResponse.of(service.get(order_id))
    except PaymentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
