"""DonationRankingPort의 wallet 구현.

`common.ranking`의 계약을 wallet이 채운다. community는 이 클래스를 모르고 wallet도
community를 모른다 — 양쪽 다 커널의 계약만 안다. 배선은 합성 루트가 한다
(`CommunityStoryFeed`·`WalletSuperchat`와 같은 형태).

**도메인 타입을 그대로 넘기지 않는다.** `DonorTotal`은 wallet의 것이므로 커널의
`DonorRank`로 옮겨 담는다 — 그러지 않으면 community가 wallet의 도메인을 알게 된다.
지금은 필드가 같아 변환이 지루해 보이지만, 이 경계 덕분에 wallet이 나중에
`DonorTotal`에 정산용 필드를 붙여도 community가 흔들리지 않는다.

**포트가 sync인 이유**는 `common.ranking`에 적혀 있다 — 소비자가 sync HTTP
핸들러라 `anyio.to_thread`가 필요 없다.
"""

from __future__ import annotations

from uuid import UUID

from aria.common.ranking import DonorRank
from aria.contexts.wallet.application.port.out.repository import DonationRepository


class WalletDonationRanking:
    def __init__(self, donations: DonationRepository) -> None:
        self._donations = donations

    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorRank]:
        return [
            DonorRank(
                donor_id=total.donor_id,
                total_amount=total.total_amount,
                donation_count=total.donation_count,
            )
            for total in self._donations.top_donors(persona_id, limit=limit)
        ]
