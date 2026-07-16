from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aria.app import create_app
from aria.common.config import settings
from aria.common.redis import get_redis
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)


def _token() -> str:
    tokens = JwtTokenService(
        settings.jwt_secret, settings.jwt_algorithm, settings.jwt_ttl_seconds
    )
    return tokens.issue_access_token(uuid4())


@pytest.fixture
def client() -> Iterator[TestClient]:
    fake = FakeAsyncRedis(decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    with TestClient(app) as test_client:
        yield test_client


def test_ws_auth_then_reply(client: TestClient) -> None:
    room, persona = uuid4(), uuid4()
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"persona_id": str(persona), "text": "안녕하세요"})
        msg = ws.receive_json()
    assert msg["accepted"] is True
    assert msg["reply"]["model_version"] == "stub"
    assert "안녕하세요" in msg["reply"]["text"]


def test_ws_rejects_bad_token(client: TestClient) -> None:
    with client.websocket_connect(f"/rooms/{uuid4()}/ws") as ws:
        ws.send_json({"token": "not-a-jwt"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_rejects_missing_token(client: TestClient) -> None:
    with client.websocket_connect(f"/rooms/{uuid4()}/ws") as ws:
        ws.send_json({"nope": "no token here"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_invalid_frame_keeps_connection(client: TestClient) -> None:
    room = uuid4()
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token()})

        # 빈 텍스트 → 도메인 검증 실패 → 에러 프레임(연결은 유지).
        ws.send_json({"persona_id": str(uuid4()), "text": ""})
        err = ws.receive_json()
        assert err["error"]["code"] == "invalid_message"

        # 이어서 정상 메시지가 여전히 처리돼야 한다.
        ws.send_json({"persona_id": str(uuid4()), "text": "다시 안녕"})
        ok = ws.receive_json()
        assert ok["accepted"] is True
        assert "다시 안녕" in ok["reply"]["text"]
