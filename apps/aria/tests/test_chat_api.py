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
from aria.contexts.chat.application.generation import RESPONSE_REQUESTED
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
def client(events: RecordingEventBus) -> Iterator[TestClient]:
    fake = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    # 생성은 워커의 일이라 여기서는 발행만 기록한다 — 브로커를 띄우지 않는다.
    app.dependency_overrides[chat_deps.get_event_bus] = lambda: events
    app.dependency_overrides[get_session] = memory_session_override()
    # context manager로 열어 요청들이 하나의 이벤트 루프를 공유하게 한다(fakeredis 루프 바인딩).
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_post_message_is_accepted_without_a_reply(
    client: TestClient, events: RecordingEventBus
) -> None:
    # C-4-1부터 응답은 요청 경로에서 나오지 않는다 — 202로 접수만 하고 생성을 맡긴다.
    persona = uuid4()
    room = live_room(client, persona)
    resp = client.post(
        f"/rooms/{room}/messages",
        json={"persona_id": str(persona), "text": "안녕하세요"},
        headers=_auth_header(),
    )

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    [event] = events.published
    assert event.stream == RESPONSE_REQUESTED
    # 키가 room_id라 같은 방의 요청은 같은 파티션 → 순서가 보장된다.
    assert event.key == str(room)
    assert event.payload["prompt"] == "안녕하세요"
    assert event.payload["source"] == "chat"


def test_rejected_message_requests_no_generation(
    client: TestClient, events: RecordingEventBus
) -> None:
    # 검증에서 걸린 메시지가 큐에 들어가면 워커가 헛돈다.
    client.post(
        f"/rooms/{uuid4()}/messages",
        json={"persona_id": str(uuid4()), "text": ""},
        headers=_auth_header(),
    )

    assert events.published == []


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
