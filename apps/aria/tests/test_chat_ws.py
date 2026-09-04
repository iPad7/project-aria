from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import direct_bus
from room_harness import live_room, memory_session_override
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
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
    # 방이 생기면서 chat도 DB를 쓴다 — 채팅은 라이브 방에서만 된다.
    app.dependency_overrides[get_session] = memory_session_override()
    with TestClient(app) as test_client:
        yield test_client


def test_ws_broadcasts_the_message_but_does_not_reply_yet(
    client: TestClient,
) -> None:
    """메시지는 즉시 방에 뜨지만 **답은 바로 오지 않는다**(FR-GEN-1·2).

    후보로 쌓이고 진행 워커가 틱마다 그중 하나를 골라 답한다. 전에는 메시지마다
    생성 요청이 나가고 슬롯 경쟁에서 이긴 것만 답했다 — 그게 "선별"이었다.
    """
    persona = uuid4()
    room = live_room(client, persona)
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token()})
        ws.send_json({"persona_id": str(persona), "text": "안녕하세요"})

        # 팬아웃으로 자기 메시지를 되받는다. 응답 프레임은 오지 않는다.
        message = ws.receive_json()

    assert message["type"] == "message"
    assert message["text"] == "안녕하세요"


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
    room = live_room(client)
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
    room = live_room(client)
    with (
        client.websocket_connect(f"/rooms/{room}/ws") as viewer,
        client.websocket_connect(f"/rooms/{room}/ws") as speaker,
    ):
        viewer.send_json({"token": _token()})
        speaker.send_json({"token": _token()})

        # viewer가 자기 메시지를 왕복시켜 구독이 성립했음을 확정한다(레이스 제거).
        viewer.send_json({"persona_id": str(uuid4()), "text": "viewer 등장"})
        assert viewer.receive_json()["text"] == "viewer 등장"  # message 이벤트

        # 이제 speaker가 보낸 것을 viewer가 받는다 — viewer는 그새 아무것도 안 보냄.
        speaker.send_json({"persona_id": str(uuid4()), "text": "다들 안녕"})
        assert viewer.receive_json()["text"] == "다들 안녕"
