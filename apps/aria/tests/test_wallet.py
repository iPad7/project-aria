"""wallet — 잔액·원장·후원.

이 슬라이스의 위험은 전부 정합성에 있다: 두 번 지급되지 않는가, 잔액이 음수가 되지
않는가, 차감과 후원 기록이 갈라지지 않는가. 테스트도 거기에 몰려 있다.

인메모리 SQLite로 hermetic하게 돈다.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelDonationRepository,
    SqlModelWalletRepository,
)
from aria.contexts.wallet.application.service import DonationService, WalletService
from aria.contexts.wallet.domain.model import (
    CreditTransaction,
    InsufficientCreditError,
    TransactionType,
)


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
def wallets(session: Session) -> WalletService:
    return WalletService(SqlModelWalletRepository(session))


@pytest.fixture
def donations(session: Session) -> DonationService:
    return DonationService(
        SqlModelWalletRepository(session), SqlModelDonationRepository(session)
    )


# --- 도메인 불변식 ---------------------------------------------------------


def test_entry_sign_must_match_type() -> None:
    # "환불인데 잔액이 늘었다" 같은 원장이 애초에 태어나지 못해야 한다.
    with pytest.raises(ValueError):
        CreditTransaction(user_id=uuid4(), delta=-100, type=TransactionType.GRANT)
    with pytest.raises(ValueError):
        CreditTransaction(user_id=uuid4(), delta=100, type=TransactionType.DONATION)


def test_entry_delta_cannot_be_zero() -> None:
    with pytest.raises(ValueError):
        CreditTransaction(user_id=uuid4(), delta=0, type=TransactionType.GRANT)


# --- 지급 ------------------------------------------------------------------


def test_balance_starts_at_zero(wallets: WalletService) -> None:
    # 지갑 로우는 첫 거래 때 생긴다. 없다고 404가 아니라 0이다.
    assert wallets.balance(uuid4()) == 0


def test_grant_increases_balance(wallets: WalletService) -> None:
    user_id = uuid4()
    assert wallets.grant(user_id, 1000, idempotency_key="k1") == 1000
    assert wallets.grant(user_id, 500, idempotency_key="k2") == 1500
    assert wallets.balance(user_id) == 1500


def test_grant_is_idempotent_by_key(wallets: WalletService) -> None:
    # 이 슬라이스에서 가장 중요한 성질 — 재시도가 두 번 지급하면 안 된다.
    user_id = uuid4()
    first = wallets.grant(user_id, 1000, idempotency_key="same-key")
    second = wallets.grant(user_id, 1000, idempotency_key="same-key")

    assert first == second == 1000
    assert wallets.balance(user_id) == 1000
    assert len(wallets.history(user_id)) == 1  # 원장에도 한 줄뿐


def test_idempotency_key_is_global_not_per_user(wallets: WalletService) -> None:
    # 유일 제약이 테이블 전역이므로 다른 사용자라도 같은 키는 두 번 적용되지 않는다.
    # 키가 요청의 식별자이지 사용자별 카운터가 아니라는 뜻이다.
    a, b = uuid4(), uuid4()
    wallets.grant(a, 1000, idempotency_key="shared")
    wallets.grant(b, 1000, idempotency_key="shared")

    assert wallets.balance(a) == 1000
    assert wallets.balance(b) == 0


def test_grant_without_key_is_not_deduplicated(wallets: WalletService) -> None:
    # 키가 없으면(NULL) 서로 중복으로 보지 않는다 — 후원처럼 매번 새 건인 경우.
    user_id = uuid4()
    wallets.grant(user_id, 100)
    wallets.grant(user_id, 100)

    assert wallets.balance(user_id) == 200


def test_history_is_newest_first(wallets: WalletService) -> None:
    user_id = uuid4()
    wallets.grant(user_id, 100, idempotency_key="k1")
    wallets.grant(user_id, 200, idempotency_key="k2")
    wallets.grant(user_id, 300, idempotency_key="k3")

    deltas = [e.delta for e in wallets.history(user_id)]

    assert deltas == [300, 200, 100]


def test_history_is_scoped_to_user(wallets: WalletService) -> None:
    mine, other = uuid4(), uuid4()
    wallets.grant(other, 100, idempotency_key="k1")

    assert wallets.history(mine) == []


# --- 후원 ------------------------------------------------------------------


def test_donate_debits_and_records(
    wallets: WalletService, donations: DonationService
) -> None:
    donor, persona = uuid4(), uuid4()
    wallets.grant(donor, 1000, idempotency_key="seed")

    donation = donations.donate(donor, persona, 300, message="화이팅")

    assert wallets.balance(donor) == 700
    listed = donations.list_for_persona(persona)
    assert len(listed) == 1
    assert listed[0].amount == 300
    assert listed[0].message == "화이팅"
    assert listed[0].id == donation.id


def test_donation_is_linked_to_ledger_entry(
    wallets: WalletService, donations: DonationService
) -> None:
    # 원장만 보고 "이 차감이 어느 후원이었나"를 답할 수 있어야 한다.
    donor, persona = uuid4(), uuid4()
    wallets.grant(donor, 1000, idempotency_key="seed")

    donation = donations.donate(donor, persona, 300)

    entry = wallets.history(donor)[0]
    assert entry.type is TransactionType.DONATION
    assert entry.delta == -300
    assert entry.ref_id == str(donation.id)


def test_donate_fails_when_balance_is_short(
    wallets: WalletService, donations: DonationService
) -> None:
    donor, persona = uuid4(), uuid4()
    wallets.grant(donor, 100, idempotency_key="seed")

    with pytest.raises(InsufficientCreditError):
        donations.donate(donor, persona, 300)


def test_failed_donation_leaves_nothing_behind(
    wallets: WalletService, donations: DonationService
) -> None:
    # 차감·원장·후원이 한 트랜잭션이라는 것의 실질적 의미 — 실패하면 전부 없다.
    donor, persona = uuid4(), uuid4()
    wallets.grant(donor, 100, idempotency_key="seed")

    with pytest.raises(InsufficientCreditError):
        donations.donate(donor, persona, 300)

    assert wallets.balance(donor) == 100
    assert donations.list_for_persona(persona) == []
    assert len(wallets.history(donor)) == 1  # 지급 한 줄만


def test_donate_with_zero_balance_fails(donations: DonationService) -> None:
    # 지갑 로우조차 없는 상태. 0행 UPDATE가 곧 잔액 부족이다.
    with pytest.raises(InsufficientCreditError):
        donations.donate(uuid4(), uuid4(), 1)


def test_donations_are_newest_first(
    wallets: WalletService, donations: DonationService
) -> None:
    donor, persona = uuid4(), uuid4()
    wallets.grant(donor, 1000, idempotency_key="seed")
    donations.donate(donor, persona, 100)
    donations.donate(donor, persona, 200)

    assert [d.amount for d in donations.list_for_persona(persona)] == [200, 100]


def test_donations_are_scoped_to_persona(
    wallets: WalletService, donations: DonationService
) -> None:
    donor, mine, other = uuid4(), uuid4(), uuid4()
    wallets.grant(donor, 1000, idempotency_key="seed")
    donations.donate(donor, other, 100)

    assert donations.list_for_persona(mine) == []


# --- HTTP ------------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


def _token(user_id: UUID, *, is_staff: bool = False) -> str:
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, 3600)
    return tokens.issue_access_token(user_id, is_staff=is_staff)


def _headers(user_id: UUID, *, is_staff: bool = False) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, is_staff=is_staff)}"}


def test_my_balance_requires_auth(client: TestClient) -> None:
    assert client.get("/wallet/me").status_code == 401


def test_my_balance_reports_zero_for_new_user(client: TestClient) -> None:
    user_id = uuid4()
    res = client.get("/wallet/me", headers=_headers(user_id))

    assert res.status_code == 200
    assert res.json() == {"user_id": str(user_id), "credit_balance": 0}


def test_grant_requires_staff(client: TestClient) -> None:
    res = client.post(
        "/wallet/grants",
        headers=_headers(uuid4()),  # 일반 사용자
        json={
            "user_id": str(uuid4()),
            "credits": 1000,
            "idempotency_key": "k1",
        },
    )

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "staff_required"


def test_staff_can_grant_and_user_sees_balance(client: TestClient) -> None:
    target = uuid4()

    granted = client.post(
        "/wallet/grants",
        headers=_headers(uuid4(), is_staff=True),
        json={
            "user_id": str(target),
            "credits": 1000,
            "idempotency_key": "k1",
            "ref_id": "TICKET-42",
        },
    )
    assert granted.status_code == 200
    assert granted.json()["credit_balance"] == 1000

    mine = client.get("/wallet/me", headers=_headers(target))
    assert mine.json()["credit_balance"] == 1000


def test_repeated_grant_request_does_not_double_credit(client: TestClient) -> None:
    target = uuid4()
    headers = _headers(uuid4(), is_staff=True)
    body = {"user_id": str(target), "credits": 1000, "idempotency_key": "dup"}

    client.post("/wallet/grants", headers=headers, json=body)
    second = client.post("/wallet/grants", headers=headers, json=body)

    assert second.json()["credit_balance"] == 1000


def test_grant_rejects_non_positive_credits(client: TestClient) -> None:
    res = client.post(
        "/wallet/grants",
        headers=_headers(uuid4(), is_staff=True),
        json={"user_id": str(uuid4()), "credits": 0, "idempotency_key": "k1"},
    )

    assert res.status_code == 422


def test_transactions_endpoint_lists_my_ledger(client: TestClient) -> None:
    target = uuid4()
    client.post(
        "/wallet/grants",
        headers=_headers(uuid4(), is_staff=True),
        json={"user_id": str(target), "credits": 700, "idempotency_key": "k1"},
    )

    res = client.get("/wallet/me/transactions", headers=_headers(target))

    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["delta"] == 700
    assert body[0]["type"] == "grant"
    assert body[0]["ref_id"] is None
    assert body[0]["created_at"] is not None


def test_transactions_endpoint_does_not_leak_other_users(client: TestClient) -> None:
    other = uuid4()
    client.post(
        "/wallet/grants",
        headers=_headers(uuid4(), is_staff=True),
        json={"user_id": str(other), "credits": 700, "idempotency_key": "k1"},
    )

    res = client.get("/wallet/me/transactions", headers=_headers(uuid4()))

    assert res.json() == []
