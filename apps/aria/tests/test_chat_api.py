from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import RecordingEventBus
from room_harness import live_room, memory_session_override
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.inbound import deps as chat_deps
from aria.contexts.chat.adapter.outbound.redis.candidates import RedisCandidateBuffer
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)


def _auth_header() -> dict[str, str]:
    # chat은 DB가 필요 없다 — get_current_principal은 JWT만 검증하므로 토큰을 직접 발급한다.
    tokens = JwtTokenService(
        settings.jwt_secret, settings.jwt_algorithm, settings.jwt_ttl_seconds
    )
    return {"Authorization": f"Bearer {tokens.issue_access_token(uuid4())}"}


@pytest.fixture
def events() -> RecordingEventBus:
    return RecordingEventBus()


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer(), decode_responses=True)


@pytest.fixture
def client(events: RecordingEventBus, redis: FakeAsyncRedis) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: redis
    # 생성은 워커의 일이라 여기서는 발행만 기록한다 — 브로커를 띄우지 않는다.
    app.dependency_overrides[chat_deps.get_event_bus] = lambda: events
    app.dependency_overrides[get_session] = memory_session_override()
    # context manager로 열어 요청들이 하나의 이벤트 루프를 공유하게 한다(fakeredis 루프 바인딩).
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


async def test_post_message_is_buffered_not_generated(
    client: TestClient, events: RecordingEventBus, redis: FakeAsyncRedis
) -> None:
    """메시지는 **후보로 쌓인다** — 곧바로 생성 요청이 나가지 않는다(FR-GEN-1·2).

    전에는 메시지마다 요청이 나가고 슬롯을 못 잡은 것은 조용히 버려졌다. 즉 "선별"이
    Redis 락 경쟁이었다. 이제 진행 워커가 틱마다 후보 중 하나를 골라 답한다.
    """
    persona = uuid4()
    room = live_room(client, persona)

    resp = client.post(
        f"/rooms/{room}/messages",
        json={"persona_id": str(persona), "text": "안녕하세요"},
        headers=_auth_header(),
    )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}
    assert events.published == []  # 생성 요청은 아직 없다

    [candidate] = await RedisCandidateBuffer(redis).take_all(room)
    assert candidate.text == "안녕하세요"


async def test_rejected_message_is_not_buffered(
    client: TestClient, events: RecordingEventBus, redis: FakeAsyncRedis
) -> None:
    # 검증에서 걸린 메시지가 후보로 쌓이면 워커가 헛돈다.
    persona = uuid4()
    room = live_room(client, persona)

    client.post(
        f"/rooms/{room}/messages",
        json={"persona_id": str(persona), "text": ""},
        headers=_auth_header(),
    )

    assert events.published == []
    assert await RedisCandidateBuffer(redis).take_all(room) == []


def test_post_requires_auth(client: TestClient) -> None:
    resp = client.post(
        f"/rooms/{uuid4()}/messages",
        json={"persona_id": str(uuid4()), "text": "hi"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


def test_post_rejects_blank_text(client: TestClient) -> None:
    resp = client.post(
        f"/rooms/{uuid4()}/messages",
        json={"persona_id": str(uuid4()), "text": ""},
        headers=_auth_header(),
    )
    assert resp.status_code == 422


def test_room_state_reflects_activity(client: TestClient) -> None:
    room = live_room(client)
    headers = _auth_header()

    before = client.get(f"/rooms/{room}/state", headers=headers).json()
    assert before["idle"] is True
    assert before["seconds_since_last"] is None

    client.post(
        f"/rooms/{room}/messages",
        json={"persona_id": str(uuid4()), "text": "안녕"},
        headers=headers,
    )

    after = client.get(f"/rooms/{room}/state", headers=headers).json()
    assert after["idle"] is False
    assert after["seconds_since_last"] is not None
