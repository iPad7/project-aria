"""wallet 조립 — 리포지토리를 유스케이스에 주입."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelDonationRepository,
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.service import DonationService, WalletService


def get_wallet_service(
    session: Annotated[Session, Depends(get_session)],
) -> WalletService:
    return WalletService(SqlModelWalletRepository(session))


def get_donation_service(
    session: Annotated[Session, Depends(get_session)],
) -> DonationService:
    # 두 리포지토리가 같은 Session을 공유해야 차감과 후원 기록이 한 트랜잭션에 든다.
    return DonationService(
        SqlModelWalletRepository(session), SqlModelDonationRepository(session)
    )
