"""DonationRankingPort — 열혈순위의 컨텍스트 간 계약.

방송국 페이지는 그 페르소나를 가장 많이 후원한 사람들을 보여준다(FR-STATION-6).
후원 기록은 `wallet`이 소유하고, 방송국 화면은 `community`가 소유한다
(`docs/architecture.md`의 `wallet -->|rankings| community`).

**왜 common에 있나.** `StoryFeedPort`·`SuperchatPort`와 같은 이유다 — 독립성 계약이
양방향이라 포트를 소비자(community) 쪽에 두면 구현자(wallet)가 그것을 import해야
하고 그 순간 `wallet ↛ community`가 깨진다. 계약은 양쪽 밖, 즉 커널에 산다.
배선은 합성 루트(`aria/app.py`).

**왜 sync인가.** 앞의 두 포트는 소비자가 chat(async)이라 async였다. 이 포트의
소비자는 community의 HTTP 핸들러이고 그건 sync 함수다 — FastAPI가 스레드풀에서
돌리므로 블로킹 DB 호출이 이벤트 루프를 막지 않는다. 여기서 async를 쓰면 구현이
`anyio.to_thread`로 sync 리포지토리를 다시 감싸야 하는데, 얻는 게 없다.

**왜 이벤트로 만든 read model 테이블이 아닌가.** `docs/data-model.md`가 랭킹을
"파생 read model, 테이블 없음"으로 둔 그대로다. 후원은 저볼륨이고 집계는 인덱스
하나로 충분하며, 테이블을 두면 갱신 누락이라는 정합성 문제를 새로 만든다. 캐시는
어댑터 쪽 데코레이터가 담당한다(좋아요 수와 같은 방식).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class DonorRank:
    """한 후원자의 누적 후원 — 순위를 매기는 데 필요한 것만."""

    donor_id: UUID
    total_amount: int
    donation_count: int


class DonationRankingPort(Protocol):
    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorRank]:
        """한 페르소나의 후원자를 누적 금액 내림차순으로. 없으면 빈 리스트.

        **후원자를 식별할 수 없는 기록은 순위에서 빠진다.** `donor_id`가 없는 후원은
        서로 다른 사람의 것이 섞여 있어 하나로 합치면 실제로 그만큼 후원한 사람이
        없는 1위를 만들어낸다.
        """
        ...
