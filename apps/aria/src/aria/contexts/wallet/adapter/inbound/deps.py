"""wallet inbound DI — 전송 중립.

HTTP가 아닌 경로로 들어오는 요청의 조립을 둔다. `SuperchatPort` 구현이 그렇다 —
chat이 포트로 호출하는 것이라 HTTP 라우터와 무관하고, 실제 배선은 합성 루트가 한다.
(community가 `StoryFeedPort`를 같은 이유로 여기에 둔다.)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelDonationRepository,
    SqlModelWalletRepository,
)
from aria.contexts.wallet.adapter.outbound.superchat import WalletSuperchat
from aria.contexts.wallet.application.service import DonationService


def get_superchat(
    session: Annotated[Session, Depends(get_session)],
) -> WalletSuperchat:
    # 두 리포지토리가 같은 Session을 공유해야 차감과 후원 기록이 한 트랜잭션에 든다.
    wallets = SqlModelWalletRepository(session)
    return WalletSuperchat(
        DonationService(wallets, SqlModelDonationRepository(session)), wallets
    )
