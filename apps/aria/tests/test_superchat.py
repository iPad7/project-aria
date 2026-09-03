"""슈퍼챗 종단 — 후원(FR-PAY-3) + 감사 응답(FR-GEN-6).

두 축을 본다:

1. **컨텍스트 간 seam** — chat이 `common.superchat` 계약만으로 wallet의 차감을 부른다.
   HTTP/WS 테스트는 `create_app()`의 실제 배선을 그대로 태우므로, 배선이 성립하는지가
   여기서 검증된다.
2. **선점** — 생성 도중 더 높은 우선순위가 끼어들면 만들어 둔 응답을 버린다. 이게 없으면
   밀려난 채팅 응답이 그대로 발행돼 우선순위가 장식이 된다.
"""

from collections.abc import Iterator, Sequence
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.common.redis import get_redis
from aria.common.superchat import SuperchatReceipt
from aria.contexts.chat.adapter.outbound.redis.coordinator import (
    RedisResponseCoordinator,
)
from aria.contexts.chat.application.port.out.llm import GenParams, LLMResult, Message
from aria.contexts.chat.application.service import ChatOrchestrationService
from aria.contexts.chat.domain.source import ChatSource
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelDonationRepository,
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.service import DonationService, WalletService

# --- 공통 픽스처 -----------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def donor_id() -> UUID:
    return uuid4()


@pytest.fixture
def client(session: Session, donor_id: UUID) -> Iterator[TestClient]:
    fake = FakeAsyncRedis(server=FakeServer(), decode_responses=True)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: fake
    app.dependency_overrides[get_session] = lambda: session
    # context manager로 열어 요청들이 하나의 이벤트 루프를 공유하게 한다.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def wallets(session: Session) -> WalletService:
    return WalletService(SqlModelWalletRepository(session))


@pytest.fixture
def donations(session: Session) -> DonationService:
    return DonationService(
        SqlModelWalletRepository(session), SqlModelDonationRepository(session)
    )


def _headers(user_id: UUID) -> dict[str, str]:
    tokens = JwtTokenService(
        settings.jwt_secret, settings.jwt_algorithm, settings.jwt_ttl_seconds
    )
    return {"Authorization": f"Bearer {tokens.issue_access_token(user_id)}"}


def _token(user_id: UUID) -> str:
    tokens = JwtTokenService(
        settings.jwt_secret, settings.jwt_algorithm, settings.jwt_ttl_seconds
    )
    return tokens.issue_access_token(user_id)


# --- HTTP: seam이 실제로 배선되는가 ----------------------------------------


def test_superchat_debits_and_replies(
    client: TestClient, wallets: WalletService, donations: DonationService, donor_id
) -> None:
    room, persona = uuid4(), uuid4()
    wallets.grant(donor_id, 1000, idempotency_key="seed")

    res = client.post(
        f"/rooms/{room}/superchats",
        json={"persona_id": str(persona), "amount": 300, "message": "응원합니다"},
        headers=_headers(donor_id),
    )

    assert res.status_code == 200
    body = res.json()
    assert body["balance_after"] == 700
    assert body["reply"] is not None
    # 후원 문구가 생성 입력에 실려 페르소나에게 전달됐다(스텁이 되돌려준다).
    assert "300" in body["reply"]["text"]
    assert "응원합니다" in body["reply"]["text"]

    recorded = donations.list_for_persona(persona)
    assert len(recorded) == 1
    assert str(recorded[0].id) == body["donation_id"]
    assert recorded[0].room_id == room  # 어느 방송에서 후원했는지 남는다


def test_superchat_without_credit_is_rejected(
    client: TestClient, wallets: WalletService, donations: DonationService, donor_id
) -> None:
    room, persona = uuid4(), uuid4()
    wallets.grant(donor_id, 100, idempotency_key="seed")

    res = client.post(
        f"/rooms/{room}/superchats",
        json={"persona_id": str(persona), "amount": 300},
        headers=_headers(donor_id),
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "insufficient_credit"
    # 차감도 기록도 없다 — 실패하면 아무 것도 남지 않는다.
    assert wallets.balance(donor_id) == 100
    assert donations.list_for_persona(persona) == []


def test_superchat_is_idempotent_with_key(
    client: TestClient, wallets: WalletService, donations: DonationService, donor_id
) -> None:
    # 재연결 후 재전송이 이중 과금되면 안 된다.
    room, persona = uuid4(), uuid4()
    wallets.grant(donor_id, 1000, idempotency_key="seed")
    body = {"persona_id": str(persona), "amount": 300, "idempotency_key": "dup"}

    client.post(f"/rooms/{room}/superchats", json=body, headers=_headers(donor_id))
    client.post(f"/rooms/{room}/superchats", json=body, headers=_headers(donor_id))

    assert wallets.balance(donor_id) == 700
    assert len(donations.list_for_persona(persona)) == 1


def test_superchat_requires_auth(client: TestClient) -> None:
    res = client.post(
        f"/rooms/{uuid4()}/superchats",
        json={"persona_id": str(uuid4()), "amount": 300},
    )
    assert res.status_code == 401


def test_superchat_rejects_non_positive_amount(
    client: TestClient, donor_id: UUID
) -> None:
    res = client.post(
        f"/rooms/{uuid4()}/superchats",
        json={"persona_id": str(uuid4()), "amount": 0},
        headers=_headers(donor_id),
    )
    assert res.status_code == 422


# --- WebSocket: 후원 표시는 항상, 응답은 조건부 ----------------------------


def test_ws_superchat_broadcasts_donation_then_reply(
    client: TestClient, wallets: WalletService, donor_id: UUID
) -> None:
    room, persona = uuid4(), uuid4()
    wallets.grant(donor_id, 1000, idempotency_key="seed")

    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token(donor_id)})
        ws.send_json(
            {
                "type": "superchat",
                "persona_id": str(persona),
                "amount": 500,
                "message": "고마워요",
            }
        )
        donation = ws.receive_json()
        reply = ws.receive_json()

    assert donation["type"] == "superchat"
    assert donation["amount"] == 500
    assert donation["donor_id"] == str(donor_id)
    assert donation["message"] == "고마워요"
    # source가 있어야 클라이언트가 감사 응답을 일반 응답과 구분한다.
    assert reply["type"] == "reply"
    assert reply["source"] == "superchat"


def test_ws_plain_message_still_works_without_type(
    client: TestClient, donor_id: UUID
) -> None:
    # type이 없으면 기존 클라이언트처럼 일반 메시지로 취급한다(하위 호환).
    room = uuid4()
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token(donor_id)})
        ws.send_json({"persona_id": str(uuid4()), "text": "안녕하세요"})

        message = ws.receive_json()
        reply = ws.receive_json()

    assert message["type"] == "message"
    assert reply["source"] == "chat"


def test_ws_superchat_without_credit_keeps_connection(
    client: TestClient, donor_id: UUID
) -> None:
    room = uuid4()
    with client.websocket_connect(f"/rooms/{room}/ws") as ws:
        ws.send_json({"token": _token(donor_id)})

        # 잔액 0 — 보낸 사람에게만 에러가 가고 방송에는 아무 것도 나가지 않는다.
        ws.send_json({"type": "superchat", "persona_id": str(uuid4()), "amount": 100})
        err = ws.receive_json()
        assert err["error"]["code"] == "insufficient_credit"

        # 연결은 살아 있다 — 이어서 일반 메시지가 정상 동작한다.
        ws.send_json({"persona_id": str(uuid4()), "text": "그래도 안녕"})
        assert ws.receive_json()["type"] == "message"


# --- 선점: 밀려난 응답을 버리는가 ------------------------------------------


class _FakeActivity:
    async def touch(self, room_id: UUID) -> None: ...

    async def is_idle(self, room_id: UUID, threshold: float) -> bool:
        return False

    async def seconds_since_last(self, room_id: UUID) -> float | None:
        return None


class _FakeSuperchat:
    """차감은 성공했다고 치는 포트 스텁 — 여기서 보려는 건 조율 로직이다."""

    def __init__(self) -> None:
        self.charges: list[int] = []

    async def charge(
        self,
        donor_id: UUID,
        persona_id: UUID,
        amount: int,
        *,
        room_id: UUID | None = None,
        message: str | None = None,
        idempotency_key: str | None = None,
    ) -> SuperchatReceipt:
        self.charges.append(amount)
        return SuperchatReceipt(donation_id=uuid4(), balance_after=700)


class _PreemptingLLM:
    """생성 '도중' 더 높은 우선순위가 끼어드는 상황을 재현한다.

    실제로는 슈퍼챗이 별도 요청으로 들어와 락을 뺏지만, 타이밍에 기대면 테스트가
    불안정해진다. 생성 시점에 직접 선점시켜 그 순간을 결정적으로 만든다.
    """

    def __init__(self, coordinator: RedisResponseCoordinator, room_id: UUID) -> None:
        self._coordinator = coordinator
        self._room_id = room_id

    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        await self._coordinator.try_acquire(self._room_id, ChatSource.SUPERCHAT)
        return LLMResult(text="밀려난 응답", model_version="stub")


class _StubLLM:
    async def generate(
        self,
        persona_id: str,
        messages: Sequence[Message],
        params: GenParams | None = None,
    ) -> LLMResult:
        return LLMResult(text="응답", model_version="stub")


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer(), decode_responses=True)


async def test_preempted_chat_reply_is_discarded(redis: FakeAsyncRedis) -> None:
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    service = ChatOrchestrationService(
        activity=_FakeActivity(),
        coordinator=coordinator,
        llm=_PreemptingLLM(coordinator, room),
        superchat=_FakeSuperchat(),
    )

    outcome = await service.handle_user_message(
        room_id=room, persona_id=uuid4(), author_id=uuid4(), text="안녕"
    )

    # 메시지는 받았지만 응답은 버린다 — 슈퍼챗이 그 사이 슬롯을 가져갔다.
    assert outcome.accepted is True
    assert outcome.reply is None


async def test_unpreempted_reply_survives(redis: FakeAsyncRedis) -> None:
    # 대조군 — 선점이 없으면 응답이 그대로 나온다.
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    service = ChatOrchestrationService(
        activity=_FakeActivity(),
        coordinator=coordinator,
        llm=_StubLLM(),
        superchat=_FakeSuperchat(),
    )

    outcome = await service.handle_user_message(
        room_id=room, persona_id=uuid4(), author_id=uuid4(), text="안녕"
    )

    assert outcome.reply is not None


async def test_superchat_stands_even_without_a_response_slot(
    redis: FakeAsyncRedis,
) -> None:
    # 같은 우선순위(슈퍼챗 둘)가 겹치면 뒤엣것은 슬롯을 못 잡는다. 그래도 차감·기록은
    # 이미 끝났으므로 후원 자체는 성립해야 한다 — 돈만 받고 사라지면 안 된다.
    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    charged = _FakeSuperchat()
    service = ChatOrchestrationService(
        activity=_FakeActivity(),
        coordinator=coordinator,
        llm=_StubLLM(),
        superchat=charged,
    )
    await coordinator.try_acquire(room, ChatSource.SUPERCHAT)  # 앞선 슈퍼챗이 점유 중

    outcome = await service.handle_superchat(
        room_id=room, persona_id=uuid4(), donor_id=uuid4(), amount=300
    )

    assert outcome.reply is None
    assert outcome.donation_id is not None
    assert charged.charges == [300]  # 차감은 일어났다


async def test_charge_failure_stops_before_any_generation(
    redis: FakeAsyncRedis,
) -> None:
    # 순서가 계약이다 — 차감이 먼저고, 실패하면 슬롯도 잡지 않는다.
    from aria.common.errors import InsufficientCreditError

    class _BrokeSuperchat:
        async def charge(self, *args: object, **kwargs: object) -> SuperchatReceipt:
            raise InsufficientCreditError("크레딧이 부족합니다")

    room = uuid4()
    coordinator = RedisResponseCoordinator(redis)
    service = ChatOrchestrationService(
        activity=_FakeActivity(),
        coordinator=coordinator,
        llm=_StubLLM(),
        superchat=_BrokeSuperchat(),
    )

    with pytest.raises(InsufficientCreditError):
        await service.handle_superchat(
            room_id=room, persona_id=uuid4(), donor_id=uuid4(), amount=300
        )

    # 슬롯이 잡히지 않았다 = 다음 요청이 곧바로 응답할 수 있다.
    assert await coordinator.try_acquire(room, ChatSource.IDLE) is not None
