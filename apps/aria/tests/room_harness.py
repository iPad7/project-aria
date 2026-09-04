"""방(Room) 이 생기면서 테스트가 공유하게 된 준비 작업.

그전까지 `room_id`는 아무 UUID나 됐고 chat은 DB를 몰랐다. 이제 채팅·후원·WS가
**라이브 방**에서만 되므로, 테스트마다 방을 하나 열어 시작해야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.common.config import settings
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)


def memory_session_override() -> Callable[[], Iterator[Session]]:
    """인메모리 SQLite를 쓰는 `get_session` 오버라이드를 만든다.

    **엔진을 한 번만 만들어 공유하는 것이 핵심이다.** 요청마다 새로 만들면
    `sqlite://`는 요청마다 빈 DB가 되어, 방을 만든 다음 요청에서 그 방을 못 찾는다.

    `StaticPool` + `check_same_thread=False`가 필요한 이유: chat의 리포지토리는
    `anyio.to_thread`로 스레드를 옮겨 다니고, SQLite는 기본적으로 커넥션을 만든
    스레드에서만 쓸 수 있다.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    return _session


def staff_headers(user_id: UUID | None = None) -> dict[str, str]:
    """운영자 토큰. 방 개설·상태 전이가 staff 전용이다(PRD FR-AUTH-3)."""
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, 3600)
    token = tokens.issue_access_token(user_id or uuid4(), is_staff=True)
    return {"Authorization": f"Bearer {token}"}


def live_room(client: TestClient, persona_id: UUID | None = None) -> UUID:
    """방을 열고 방송을 시작해 그 id를 돌려준다.

    개설 직후는 `pending`이라 채팅을 받지 않는다 — 시작까지 해야 한다.
    """
    headers = staff_headers()
    created = client.post(
        "/rooms",
        headers=headers,
        json={"persona_id": str(persona_id or uuid4()), "name": "테스트 방송"},
    )
    assert created.status_code == 201, created.text
    room_id = created.json()["id"]

    started = client.post(f"/rooms/{room_id}/live", headers=headers)
    assert started.status_code == 200, started.text
    return UUID(room_id)
