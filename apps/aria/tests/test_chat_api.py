from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.redis import get_redis
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
def client() -> Iterator[TestClient]:
    fake = FakeAsyncRedis(decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    # context manager로 열어 요청들이 하나의 이벤트 루프를 공유하게 한다(fakeredis 루프 바인딩).
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_post_message_gets_stub_reply(client: TestClient) -> None:
    room, persona = uuid4(), uuid4()
    resp = client.post(
        f"/rooms/{room}/messages",
        json={"persona_id": str(persona), "text": "안녕하세요"},
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["reply"]["model_version"] == "stub"
    assert "안녕하세요" in body["reply"]["text"]


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
    room = uuid4()
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
