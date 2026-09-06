"""방(Room) — 방송 개설·상태 전이·검증.

이 단위의 위험은 셋이다:

1. **상태 전이** — 끝난 방송이 다시 살아나거나, 한 페르소나가 두 방송을 동시에 하거나.
2. **권한** — 아무나 남의 페르소나로 방송을 열거나.
3. **검증 누락** — 존재하지 않는 방에 크레딧이 태워지거나. 차감은 진짜로 일어난다.

인메모리 SQLite + fakeredis로 hermetic하게 돈다.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from generation_harness import RecordingEventBus
from room_harness import live_room, memory_session_override, staff_headers
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.common.redis import get_redis
from aria.contexts.chat.adapter.inbound import deps as chat_deps
from aria.contexts.chat.domain.room import (
    InvalidRoomTransition,
    Room,
    RoomStatus,
)
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)

# --- 도메인 불변식 ---------------------------------------------------------


def _room(status: RoomStatus = RoomStatus.PENDING) -> Room:
    return Room(persona_id=uuid4(), host_id=uuid4(), name="테스트 방송", status=status)


def test_pending_room_is_not_open_for_chat() -> None:
    # 개설만 해 둔 방은 아직 말을 받지 않는다.
    assert _room().is_open_for_chat() is False


def test_live_room_is_open_for_chat() -> None:
    assert _room(RoomStatus.LIVE).is_open_for_chat() is True


def test_finished_room_is_not_open_for_chat() -> None:
    assert _room(RoomStatus.FINISHED).is_open_for_chat() is False


def test_finished_room_cannot_go_back_to_live() -> None:
    # 끝난 방송이 다시 살아나는 상태는 시청자·정산·아카이브 어디에도 설명이 안 된다.
    room = _room(RoomStatus.FINISHED)
    with pytest.raises(InvalidRoomTransition):
        room.transition_to(RoomStatus.LIVE)


def test_live_room_cannot_restart() -> None:
    # 멱등하게 넘기지 않는다 — 대개 클라이언트가 뭔가 잘못 알고 있다는 뜻이다.
    room = _room(RoomStatus.LIVE)
    with pytest.raises(InvalidRoomTransition):
        room.transition_to(RoomStatus.LIVE)


def test_pending_room_can_be_finished_without_going_live() -> None:
    # 열어 두고 취소하는 경우. 앞으로만 가면 된다.
    room = _room()
    room.transition_to(RoomStatus.FINISHED)
    assert room.status is RoomStatus.FINISHED


def test_finishing_records_when_the_broadcast_ended() -> None:
    """종료 경로가 둘(운영자의 finish, 방치 정리)이라 도메인이 직접 찍는다.

    호출자에게 맡기면 한쪽이 빠뜨리고, 그러면 "언제 끝났나"를 답할 수 없는 방이 생긴다.
    """
    room = _room(RoomStatus.LIVE)
    assert room.closed_at is None

    room.transition_to(RoomStatus.FINISHED)

    assert room.closed_at is not None


def test_room_name_cannot_be_blank() -> None:
    with pytest.raises(ValueError):
        Room(persona_id=uuid4(), host_id=uuid4(), name="")


# --- HTTP ------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    fake = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    app.dependency_overrides[get_session] = memory_session_override()
    app.dependency_overrides[chat_deps.get_event_bus] = lambda: RecordingEventBus()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _viewer_headers(user_id: UUID | None = None) -> dict[str, str]:
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, 3600)
    return {"Authorization": f"Bearer {tokens.issue_access_token(user_id or uuid4())}"}


def _open(client: TestClient, persona_id: UUID) -> str:
    res = client.post(
        "/rooms",
        headers=staff_headers(),
        json={"persona_id": str(persona_id), "name": "오늘의 연애상담"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_opening_a_room_requires_staff(client: TestClient) -> None:
    # chat은 persona 소유권을 확인할 수 없어 개설을 운영자로 좁혔다(PRD FR-AUTH-3).
    res = client.post(
        "/rooms",
        headers=_viewer_headers(),
        json={"persona_id": str(uuid4()), "name": "몰래 여는 방송"},
    )

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "staff_required"


def test_opened_room_starts_pending(client: TestClient) -> None:
    res = client.get(f"/rooms/{_open(client, uuid4())}")

    assert res.json()["status"] == "pending"


def test_starting_a_broadcast_makes_it_live(client: TestClient) -> None:
    room_id = _open(client, uuid4())

    res = client.post(f"/rooms/{room_id}/live", headers=staff_headers())

    assert res.status_code == 200
    assert res.json()["status"] == "live"


def test_one_persona_cannot_broadcast_twice_at_once(client: TestClient) -> None:
    # 스트리머는 한 번에 하나만 방송한다. 부분 유일 인덱스가 강제한다.
    persona = uuid4()
    client.post(f"/rooms/{_open(client, persona)}/live", headers=staff_headers())
    second = _open(client, persona)  # 개설까지는 된다 — pending이므로

    res = client.post(f"/rooms/{second}/live", headers=staff_headers())

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "already_live"


def test_persona_can_broadcast_again_after_finishing(client: TestClient) -> None:
    # 유일성이 live에만 걸려야 한다 — 그냥 unique면 두 번째 방송을 영영 못 연다.
    persona = uuid4()
    first = _open(client, persona)
    client.post(f"/rooms/{first}/live", headers=staff_headers())
    client.post(f"/rooms/{first}/finish", headers=staff_headers())

    res = client.post(f"/rooms/{_open(client, persona)}/live", headers=staff_headers())

    assert res.status_code == 200


def test_finishing_a_broadcast_persists_when_it_ended(client: TestClient) -> None:
    # 도메인이 찍은 값이 실제로 저장되고 다시 읽히는지 — 컬럼 하나가 빠지면 조용히 사라진다.
    room_id = _open(client, uuid4())
    client.post(f"/rooms/{room_id}/live", headers=staff_headers())
    assert client.get(f"/rooms/{room_id}").json()["closed_at"] is None

    client.post(f"/rooms/{room_id}/finish", headers=staff_headers())

    assert client.get(f"/rooms/{room_id}").json()["closed_at"] is not None


def test_finished_broadcast_cannot_restart(client: TestClient) -> None:
    room_id = _open(client, uuid4())
    client.post(f"/rooms/{room_id}/live", headers=staff_headers())
    client.post(f"/rooms/{room_id}/finish", headers=staff_headers())

    res = client.post(f"/rooms/{room_id}/live", headers=staff_headers())

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_room_transition"


def test_transitions_require_staff(client: TestClient) -> None:
    room_id = _open(client, uuid4())

    assert (
        client.post(f"/rooms/{room_id}/live", headers=_viewer_headers()).status_code
        == 403
    )


def test_live_list_is_public_and_shows_only_live(client: TestClient) -> None:
    pending = _open(client, uuid4())
    live = _open(client, uuid4())
    client.post(f"/rooms/{live}/live", headers=staff_headers())

    listed = client.get("/rooms").json()  # 인증 헤더 없음 — 공개다

    assert [r["id"] for r in listed] == [live]
    assert pending not in [r["id"] for r in listed]


def test_unknown_room_is_not_found(client: TestClient) -> None:
    res = client.get(f"/rooms/{uuid4()}")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "room_not_found"


# --- 검증: 라이브 방에서만 말할 수 있다 --------------------------------------


def test_message_to_unknown_room_is_rejected(client: TestClient) -> None:
    res = client.post(
        f"/rooms/{uuid4()}/messages",
        headers=_viewer_headers(),
        json={"persona_id": str(uuid4()), "text": "아무도 없나요"},
    )

    assert res.status_code == 404


def test_message_to_pending_room_is_rejected(client: TestClient) -> None:
    # 개설만 해 둔 방송의 존재를 밖으로 흘리지 않는다 — 없는 것과 같은 응답이다.
    res = client.post(
        f"/rooms/{_open(client, uuid4())}/messages",
        headers=_viewer_headers(),
        json={"persona_id": str(uuid4()), "text": "미리 왔어요"},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "room_not_live"


def test_superchat_to_unknown_room_is_rejected_before_charging(
    client: TestClient,
) -> None:
    """이 단위의 존재 이유. 그전까지는 없는 방에 크레딧을 태울 수 있었다.

    차감은 진짜로 일어나고 `wallet_donation`에 기록도 남았다 — 방을 확인하지 않으면
    돈만 빠져나간다.
    """
    donor = uuid4()
    client.post(
        "/wallet/grants",
        headers=staff_headers(),
        json={"user_id": str(donor), "credits": 1000, "idempotency_key": "seed"},
    )

    res = client.post(
        f"/rooms/{uuid4()}/superchats",
        headers=_viewer_headers(donor),
        json={"persona_id": str(uuid4()), "amount": 300},
    )

    assert res.status_code == 404
    # 차감이 없었다 — 검증이 차감보다 앞선다.
    balance = client.get("/wallet/me", headers=_viewer_headers(donor)).json()
    assert balance["credit_balance"] == 1000


def test_ws_rejects_a_room_that_is_not_live(client: TestClient) -> None:
    room_id = _open(client, uuid4())  # pending

    with client.websocket_connect(f"/rooms/{room_id}/ws") as ws:
        ws.send_json({"token": _viewer_headers()["Authorization"].split()[1]})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()

    assert exc.value.code == 4404


def test_ws_accepts_a_live_room(client: TestClient) -> None:
    room = live_room(client)

    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _viewer_headers()["Authorization"].split()[1]})
        ws.send_json({"persona_id": str(uuid4()), "text": "안녕하세요"})

        assert ws.receive_json()["type"] == "message"


# --- 페르소나는 방이 정한다 (사후 발견) --------------------------------------


def test_message_uses_the_rooms_persona_not_the_clients(
    client: TestClient,
) -> None:
    """방(#53)이 생기기 전에는 클라이언트가 매 메시지에 `persona_id` 를 실어 보냈다.

    지금은 방이 페르소나를 소유한다. 받아 두고 무시하면 잘못 보내도 아무 일이 안
    일어나 디버깅이 어려워지므로(거짓 계약), 아예 받지 않는다.
    """
    room_id = _open(client, uuid4())
    client.post(f"/rooms/{room_id}/live", headers=staff_headers())

    res = client.post(
        f"/rooms/{room_id}/messages",
        headers=_viewer_headers(),
        # persona_id 를 보내지 않아도 된다 — 방에서 가져온다.
        json={"text": "안녕하세요"},
    )

    assert res.status_code == 202


def test_superchat_is_recorded_against_the_rooms_persona(
    client: TestClient,
) -> None:
    """돈이 걸린 경로라 특히 중요하다.

    클라이언트가 보낸 값을 그대로 믿으면 **엉뚱한 페르소나에게 후원이 기록된다**
    (`wallet_donation.persona_id`) — 열혈순위가 그 값을 집계한다.
    """
    persona = uuid4()
    room_id = _open(client, persona)
    client.post(f"/rooms/{room_id}/live", headers=staff_headers())
    donor = uuid4()
    client.post(
        "/wallet/grants",
        headers=staff_headers(),
        json={"user_id": str(donor), "credits": 1000, "idempotency_key": "seed"},
    )

    res = client.post(
        f"/rooms/{room_id}/superchats",
        headers=_viewer_headers(donor),
        json={"amount": 300},
    )
    assert res.status_code == 200, res.text

    # 후원이 **방의** 페르소나 순위에 잡힌다.
    board = client.get(f"/personas/{persona}/ranking").json()
    assert [r["donor_id"] for r in board] == [str(donor)]
