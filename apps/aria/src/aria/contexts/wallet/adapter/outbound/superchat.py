"""SuperchatPort의 wallet 구현.

`common.superchat`의 계약을 wallet이 채운다. chat은 이 클래스를 모르고, wallet도 chat을
모른다 — 양쪽 다 common의 계약만 안다. 배선은 합성 루트(`aria/app.py`)가 한다.

**async 포트를 sync 영속 계층으로 구현한다.** chat의 포트는 전부 async인데 wallet의
리포지토리는 sync(SQLModel Session)다. 블로킹 DB 호출을 이벤트 루프에서 그대로 하면
루프가 멈추므로 `anyio.to_thread`로 넘긴다 — `CommunityStoryFeed`와 같은 방식이다.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

import anyio.to_thread

from aria.common.superchat import SuperchatReceipt
from aria.contexts.wallet.application.port.out.repository import WalletRepository
from aria.contexts.wallet.application.service import DonationService


class WalletSuperchat:
    def __init__(self, donations: DonationService, wallets: WalletRepository) -> None:
        self._donations = donations
        # 차감 후 잔액을 돌려주려면 잔액을 한 번 더 읽어야 한다. DonationService가
        # Donation을 돌려주는 것이 자연스러운 계약이라, 잔액은 여기서 조회한다.
        self._wallets = wallets

    async def charge(
        self,
        donor_id: UUID,
        persona_id: UUID,
        amount: int,
        *,
        room_id: UUID | None = None,
        message: str | None = None,
        idempotency_key: str | None = None,
    ) -> SuperchatReceipt:
        donation = await anyio.to_thread.run_sync(
            partial(
                self._donations.donate,
                donor_id,
                persona_id,
                amount,
                message=message,
                room_id=room_id,
                idempotency_key=idempotency_key,
            )
        )
        balance = await anyio.to_thread.run_sync(self._wallets.balance_of, donor_id)
        return SuperchatReceipt(donation_id=donation.id, balance_after=balance)
