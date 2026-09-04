"""UserDirectoryPort — 사용자 표시명 조회의 컨텍스트 간 계약.

UUID만 나열된 순위표는 화면에서 쓸모가 없다. 그런데 표시명(`username`)은 identity가
소유하고, community도 wallet도 identity를 import할 수 없다. 그래서 "id 여러 개를
이름으로 바꾼다"는 좁은 계약만 커널에 두고 identity가 채운다.

**왜 벌크인가.** 순위 하나당 한 번씩 조회하면 N+1이 된다. 이름을 붙이자고 랭킹
조회를 20배 느리게 만들 이유가 없으므로 포트 자체를 여러 건 단위로 못박는다.

**왜 이름만인가.** 이메일·권한 같은 것까지 열면 이 포트가 identity의 우회 통로가
된다. 공개 화면에 찍히는 표시명은 identity가 어차피 공개를 전제로 다루는 값이고,
그 이상은 필요할 때 별도 계약으로 연다.

배선은 합성 루트(`aria/app.py`). sync인 이유는 `common.ranking`과 같다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class UserDirectoryPort(Protocol):
    def display_names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """주어진 id들의 표시명. **찾지 못한 id는 결과에 없다.**

        빈 문자열이나 "(알 수 없음)" 같은 것을 여기서 만들어 내지 않는다 — 탈퇴한
        사용자를 어떻게 보여줄지는 화면의 결정이고, 이 포트는 사실만 돌려준다.
        """
        ...
