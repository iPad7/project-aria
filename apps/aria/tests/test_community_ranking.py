"""열혈순위 — 방송국 후원자 랭킹(FR-STATION-6).

이 슬라이스의 위험은 세 곳에 있다:

1. **집계** — 익명 후원을 한 사람으로 합치거나, 동점 순서가 요청마다 달라지거나.
2. **컨텍스트 간 seam** — 금액은 wallet, 이름은 identity, 화면은 community다. 셋을
   잇는 배선이 성립하는지는 `create_app()`을 그대로 태우는 HTTP 테스트가 검증한다.
3. **캐시** — 성능 장치가 정확성을 갉아먹지 않는가(limit별 분리, 장애 시 폴백).

인메모리 SQLite + fakeredis(sync)로 hermetic하게 돈다.
"""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis, FakeRedis, FakeServer
from redis import RedisError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.config import settings
from aria.common.db import get_session
from aria.common.ranking import DonorRank
from aria.common.redis import get_redis, get_sync_redis
from aria.contexts.community.application.service import (
    MAX_RANKING_SIZE,
    RankingService,
)
from aria.contexts.identity.adapter.outbound.security.jwt_token_service import (
    JwtTokenService,
)
from aria.contexts.wallet.adapter.outbound.cache.donation_ranking import (
    CachedDonationRepository,
)
from aria.contexts.wallet.adapter.outbound.persistence.model import DonationTable
from aria.contexts.wallet.adapter.outbound.persistence.repository import (
    SqlModelDonationRepository,
)
from aria.contexts.wallet.domain.model import DonorTotal

# --- 픽스처 ----------------------------------------------------------------


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
def donations(session: Session) -> SqlModelDonationRepository:
    return SqlModelDonationRepository(session)


def _record(
    session: Session,
    persona_id: UUID,
    donor_id: UUID | None,
    amount: int,
    *,
    at: datetime | None = None,
) -> None:
    """후원 로우를 직접 넣는다.

    차감 경로(`WalletRepository.apply`)를 거치지 않는 이유는 여기서 보려는 것이
    **집계**이기 때문이다 — 잔액을 채워 두는 준비가 길어지면 정작 검증할 것이 묻힌다.
    차감과 함께 도는 실제 경로는 아래 HTTP 테스트가 종단으로 확인한다.
    """
    row = DonationTable(persona_id=persona_id, donor_id=donor_id, amount=amount)
    if at is not None:
        row.created_at = at
    session.add(row)
    session.commit()


# --- 집계 ------------------------------------------------------------------


def test_ranking_is_empty_without_donations(
    donations: SqlModelDonationRepository,
) -> None:
    assert donations.top_donors(uuid4(), limit=10) == []


def test_totals_are_summed_per_donor(
    session: Session, donations: SqlModelDonationRepository
) -> None:
    persona, big, small = uuid4(), uuid4(), uuid4()
    _record(session, persona, small, 500)
    _record(session, persona, big, 300)
    _record(session, persona, big, 300)  # 합 600 — 한 번에 500을 낸 사람보다 앞

    ranking = donations.top_donors(persona, limit=10)

    assert [(r.donor_id, r.total_amount, r.donation_count) for r in ranking] == [
        (big, 600, 2),
        (small, 500, 1),
    ]


def test_other_personas_do_not_leak_into_ranking(
    session: Session, donations: SqlModelDonationRepository
) -> None:
    mine, other, donor = uuid4(), uuid4(), uuid4()
    _record(session, mine, donor, 100)
    _record(session, other, donor, 9_999)

    ranking = donations.top_donors(mine, limit=10)

    assert [(r.donor_id, r.total_amount) for r in ranking] == [(donor, 100)]


def test_anonymous_donations_are_excluded(
    session: Session, donations: SqlModelDonationRepository
) -> None:
    # 서로 다른 사람의 익명 후원을 하나로 합치면 실재하지 않는 1위가 만들어진다.
    persona, named = uuid4(), uuid4()
    _record(session, persona, None, 5_000)
    _record(session, persona, None, 5_000)
    _record(session, persona, named, 100)

    ranking = donations.top_donors(persona, limit=10)

    assert [r.donor_id for r in ranking] == [named]


def test_ties_are_broken_by_first_donation(
    session: Session, donations: SqlModelDonationRepository
) -> None:
    # 기준이 금액 하나뿐이면 동점 순서가 실행마다 달라져 화면이 이유 없이 흔들린다.
    persona, early, late = uuid4(), uuid4(), uuid4()
    _record(session, persona, late, 100, at=datetime(2026, 9, 2, tzinfo=UTC))
    _record(session, persona, early, 100, at=datetime(2026, 9, 1, tzinfo=UTC))

    assert [r.donor_id for r in donations.top_donors(persona, limit=10)] == [
        early,
        late,
    ]


def test_limit_truncates_from_the_top(
    session: Session, donations: SqlModelDonationRepository
) -> None:
    persona = uuid4()
    donors = [uuid4() for _ in range(3)]
    for rank, donor in enumerate(donors):
        _record(session, persona, donor, 300 - rank * 100)

    ranking = donations.top_donors(persona, limit=2)

    assert [r.donor_id for r in ranking] == donors[:2]


# --- 이름 붙이기 (RankingService) -------------------------------------------


class _FakeRanking:
    def __init__(self, ranks: list[DonorRank]) -> None:
        self._ranks = ranks
        self.asked_limit: int | None = None

    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorRank]:
        self.asked_limit = limit
        return self._ranks[:limit]


class _FakeDirectory:
    def __init__(self, names: dict[UUID, str]) -> None:
        self._names = names
        self.calls: list[Sequence[UUID]] = []

    def display_names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        self.calls.append(list(user_ids))
        return {uid: self._names[uid] for uid in user_ids if uid in self._names}


def test_supporters_get_names_and_consecutive_ranks() -> None:
    first, second = uuid4(), uuid4()
    service = RankingService(
        _FakeRanking(
            [
                DonorRank(donor_id=first, total_amount=900, donation_count=3),
                DonorRank(donor_id=second, total_amount=100, donation_count=1),
            ]
        ),
        _FakeDirectory({first: "열혈팬", second: "지나가던_시청자"}),
    )

    supporters = service.top_supporters(uuid4())

    assert [(s.rank, s.display_name, s.total_amount) for s in supporters] == [
        (1, "열혈팬", 900),
        (2, "지나가던_시청자", 100),
    ]


def test_withdrawn_user_keeps_rank_without_a_name() -> None:
    # 이름이 없다고 순위에서 빼면 아래 순위가 한 칸씩 올라가 실제 순위가 아니게 된다.
    gone, alive = uuid4(), uuid4()
    service = RankingService(
        _FakeRanking(
            [
                DonorRank(donor_id=gone, total_amount=900, donation_count=1),
                DonorRank(donor_id=alive, total_amount=100, donation_count=1),
            ]
        ),
        _FakeDirectory({alive: "남은사람"}),
    )

    supporters = service.top_supporters(uuid4())

    assert [(s.rank, s.display_name) for s in supporters] == [
        (1, None),
        (2, "남은사람"),
    ]


def test_names_are_fetched_in_one_call() -> None:
    # 순위 한 줄마다 조회하면 N+1이 된다 — 포트를 벌크로 못박은 이유다.
    donors = [uuid4() for _ in range(3)]
    directory = _FakeDirectory(dict.fromkeys(donors, "누군가"))
    service = RankingService(
        _FakeRanking(
            [DonorRank(donor_id=d, total_amount=100, donation_count=1) for d in donors]
        ),
        directory,
    )

    service.top_supporters(uuid4())

    assert len(directory.calls) == 1
    assert set(directory.calls[0]) == set(donors)


def test_empty_ranking_skips_the_directory() -> None:
    directory = _FakeDirectory({})
    service = RankingService(_FakeRanking([]), directory)

    assert service.top_supporters(uuid4()) == []
    assert directory.calls == []


def test_limit_is_clamped_to_the_maximum() -> None:
    ranking = _FakeRanking([])
    RankingService(ranking, _FakeDirectory({})).top_supporters(uuid4(), limit=10_000)

    assert ranking.asked_limit == MAX_RANKING_SIZE


# --- 캐시 ------------------------------------------------------------------


class _CountingDonations:
    def __init__(self, ranking: list[DonorTotal]) -> None:
        self._ranking = ranking
        self.hits = 0

    def list_by_persona(self, persona_id: UUID, *, limit: int, offset: int) -> list:
        return []

    def top_donors(self, persona_id: UUID, *, limit: int) -> list[DonorTotal]:
        self.hits += 1
        return self._ranking[:limit]


def _totals(count: int) -> list[DonorTotal]:
    return [
        DonorTotal(donor_id=uuid4(), total_amount=100 * (count - i), donation_count=1)
        for i in range(count)
    ]


@pytest.fixture
def sync_redis() -> FakeRedis:
    return FakeRedis(server=FakeServer(), decode_responses=True)


def test_second_read_is_served_from_cache(sync_redis: FakeRedis) -> None:
    inner = _CountingDonations(_totals(2))
    cached = CachedDonationRepository(inner, sync_redis)
    persona = uuid4()

    first = cached.top_donors(persona, limit=10)
    second = cached.top_donors(persona, limit=10)

    assert inner.hits == 1
    assert first == second


def test_cache_does_not_answer_a_wider_request(sync_redis: FakeRedis) -> None:
    # 상위 2명을 캐시해 두고 상위 10명 요청에 답하면 있는 사람을 빠뜨린 순위를 준다.
    inner = _CountingDonations(_totals(5))
    cached = CachedDonationRepository(inner, sync_redis)
    persona = uuid4()

    cached.top_donors(persona, limit=2)
    wider = cached.top_donors(persona, limit=10)

    assert inner.hits == 2
    assert len(wider) == 5


def test_cache_failure_falls_back_to_the_database() -> None:
    class _BrokenRedis:
        def get(self, key: str) -> None:
            raise RedisError("down")

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            raise RedisError("down")

    inner = _CountingDonations(_totals(1))
    cached = CachedDonationRepository(inner, _BrokenRedis())  # type: ignore[arg-type]

    # 캐시는 성능 장치일 뿐이라, Redis가 죽어도 순위는 계속 보여야 한다.
    assert len(cached.top_donors(uuid4(), limit=10)) == 1
    assert inner.hits == 1


def test_corrupt_cache_entry_is_discarded(sync_redis: FakeRedis) -> None:
    inner = _CountingDonations(_totals(1))
    cached = CachedDonationRepository(inner, sync_redis)
    persona = uuid4()
    cached.top_donors(persona, limit=10)
    sync_redis.set(f"wallet:donation_ranking:{persona}:10", "not json")

    assert len(cached.top_donors(persona, limit=10)) == 1
    assert inner.hits == 2  # 손상된 값을 믿지 않고 DB로 다시 갔다


# --- HTTP 종단 (컨텍스트 간 배선) ---------------------------------------------


@pytest.fixture
def client(session: Session, sync_redis: FakeRedis) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_sync_redis] = lambda: sync_redis
    app.dependency_overrides[get_redis] = lambda: FakeAsyncRedis(
        server=FakeServer(), decode_responses=True
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _staff_headers() -> dict[str, str]:
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, 3600)
    return {
        "Authorization": f"Bearer {tokens.issue_access_token(uuid4(), is_staff=True)}"
    }


def _register(client: TestClient, username: str) -> tuple[UUID, dict[str, str]]:
    email = f"{username}@example.com"
    body = {"email": email, "username": username, "password": "s3cret-pw"}
    user_id = UUID(client.post("/auth/register", json=body).json()["id"])
    token = client.post(
        "/auth/login", json={"email": email, "password": "s3cret-pw"}
    ).json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


def test_ranking_endpoint_is_public(client: TestClient) -> None:
    # 비로그인 시청자도 방송국을 본다(FR-STATION-1과 같은 이유).
    res = client.get(f"/personas/{uuid4()}/ranking")

    assert res.status_code == 200
    assert res.json() == []


def test_donating_puts_a_named_supporter_on_the_board(client: TestClient) -> None:
    """차감(wallet) → 집계(wallet) → 이름(identity) → 화면(community) 전 구간.

    세 컨텍스트가 서로를 import하지 않고도 한 화면을 만들어 내는지가 여기서 드러난다.
    배선이 빠지면 `NotImplementedError`로 500이 난다.
    """
    persona_id, room_id = uuid4(), uuid4()
    donor_id, donor_headers = _register(client, "열혈시청자")
    client.post(
        "/wallet/grants",
        headers=_staff_headers(),
        json={
            "user_id": str(donor_id),
            "credits": 1_000,
            "idempotency_key": f"seed-{donor_id}",
        },
    )

    donated = client.post(
        f"/rooms/{room_id}/superchats",
        headers=donor_headers,
        json={"persona_id": str(persona_id), "amount": 700, "message": "화이팅"},
    )
    assert donated.status_code == 200, donated.text

    board = client.get(f"/personas/{persona_id}/ranking").json()

    assert board == [
        {
            "rank": 1,
            "donor_id": str(donor_id),
            "display_name": "열혈시청자",
            "total_amount": 700,
            "donation_count": 1,
        }
    ]


def test_ranking_rejects_an_oversized_limit(client: TestClient) -> None:
    res = client.get(f"/personas/{uuid4()}/ranking", params={"limit": 500})

    assert res.status_code == 422
