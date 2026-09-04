from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import direct_bus
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aria.app import create_app
from aria.common.config import settings
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.inbound import deps as chat_deps
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
    # 테스트마다 독립 서버 — pub/sub 구독이 다른 테스트로 새지 않게 격리한다.
    fake = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    # 워커를 같은 프로세스에서 태운다 — 응답은 이제 워커가 만들어 방 채널로 발행한다.
    app.dependency_overrides[chat_deps.get_event_bus] = lambda: direct_bus(fake)
    with TestClient(app) as test_client:
        yield test_client


def test_ws_broadcasts_message_then_reply(client: TestClient) -> None:
    room, persona = uuid4(), uuid4()
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"persona_id": str(persona), "text": "안녕하세요"})

        # 팬아웃으로 자기 메시지 이벤트 → 페르소나 응답 이벤트 순서로 되받는다.
        message = ws.receive_json()
        reply = ws.receive_json()

    assert message["type"] == "message"
    assert message["text"] == "안녕하세요"
    assert reply["type"] == "reply"
    assert reply["model_version"] == "stub"
    assert "안녕하세요" in reply["text"]


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

        # 빈 텍스트 → 도메인 검증 실패 → 보낸 사람에게 에러 프레임(발행 안 함, 연결 유지).
        ws.send_json({"persona_id": str(uuid4()), "text": ""})
        err = ws.receive_json()
        assert err["error"]["code"] == "invalid_message"

        # 이어서 정상 메시지는 여전히 팬아웃된다.
        ws.send_json({"persona_id": str(uuid4()), "text": "다시 안녕"})
        message = ws.receive_json()
        assert message["type"] == "message"
        assert message["text"] == "다시 안녕"


def test_ws_two_clients_same_room_see_each_other(client: TestClient) -> None:
    room = uuid4()
    with (
        client.websocket_connect(f"/rooms/{room}/ws") as viewer,
        client.websocket_connect(f"/rooms/{room}/ws") as speaker,
    ):
        viewer.send_json({"token": _token()})
        speaker.send_json({"token": _token()})

        # viewer가 자기 메시지를 왕복시켜 구독이 성립했음을 확정한다(레이스 제거).
        viewer.send_json({"persona_id": str(uuid4()), "text": "viewer 등장"})
        assert viewer.receive_json()["text"] == "viewer 등장"  # message 이벤트
        viewer.receive_json()  # reply 이벤트 배수

        # 이제 speaker가 보낸 것을 viewer가 받는다 — viewer는 그새 아무것도 안 보냄.
        speaker.send_json({"persona_id": str(uuid4()), "text": "다들 안녕"})
        while (event := viewer.receive_json())["type"] != "message":
            pass  # 혹시 낀 reply 프레임은 건너뛴다(발행 순서상 message가 먼저)
        assert event["text"] == "다들 안녕"
