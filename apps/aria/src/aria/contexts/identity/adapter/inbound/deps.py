"""identity inbound DI — 전송 중립.

HTTP가 아닌 경로로 들어오는 요청의 조립을 둔다. `UserDirectoryPort` 구현이 그렇다 —
다른 컨텍스트가 포트로 부르는 것이라 로그인·회원가입 라우터와 무관하고, 실제 배선은
합성 루트가 한다. (wallet의 `SuperchatPort`, community의 `StoryFeedPort`와 같은 자리.)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from aria.common.db import get_session
from aria.contexts.identity.adapter.outbound.persistence.repository import (
    SqlModelUserRepository,
)
from aria.contexts.identity.adapter.outbound.user_directory import IdentityUserDirectory


def get_user_directory(
    session: Annotated[Session, Depends(get_session)],
) -> IdentityUserDirectory:
    return IdentityUserDirectory(SqlModelUserRepository(session))
