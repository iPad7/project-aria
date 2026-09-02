"""좋아요 + 방송국 공개 프로필 테스트.

인메모리 SQLite + fakeredis(sync)로 hermetic하게 돈다.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakeredis import FakeRedis, FakeServer
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.db import get_session
from aria.common.redis import get_sync_redis


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis(server=FakeServer(), decode_responses=True)


@pytest.fixture
def client(redis: FakeRedis) -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    app = create_app()

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_sync_redis] = lambda: redis
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers(client: TestClient, email: str) -> dict[str, str]:
    client.post(
        "/auth/register",
        json={
            "email": email,
            "username": email.split("@")[0] + "_user",
            "password": "s3cret-pw",
        },
    )
    token = client.post(
        "/auth/login", json={"email": email, "password": "s3cret-pw"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _count(client: TestClient, persona_id: str) -> int:
    return client.get(f"/personas/{persona_id}/likes").json()["count"]


def test_like_then_count(client: TestClient) -> None:
    headers = _auth_headers(client, "a@example.com")
    persona_id = str(uuid4())

    res = client.put(f"/personas/{persona_id}/likes", headers=headers)

    assert res.status_code == 204
    assert _count(client, persona_id) == 1


def test_like_is_idempotent(client: TestClient) -> None:
    # 더블탭이나 재시도로 두 번 와도 좋아요가 취소되면 안 된다 — 토글이 아닌 이유.
    headers = _auth_headers(client, "b@example.com")
    persona_id = str(uuid4())

    client.put(f"/personas/{persona_id}/likes", headers=headers)
    res = client.put(f"/personas/{persona_id}/likes", headers=headers)

    assert res.status_code == 204
    assert _count(client, persona_id) == 1


def test_unlike_is_idempotent(client: TestClient) -> None:
    headers = _auth_headers(client, "c@example.com")
    persona_id = str(uuid4())
    client.put(f"/personas/{persona_id}/likes", headers=headers)

    client.delete(f"/personas/{persona_id}/likes", headers=headers)
    res = client.delete(f"/personas/{persona_id}/likes", headers=headers)

    assert res.status_code == 204
    assert _count(client, persona_id) == 0


def test_likes_are_per_user(client: TestClient) -> None:
    alice = _auth_headers(client, "alice@example.com")
    bob = _auth_headers(client, "bob@example.com")
    persona_id = str(uuid4())

    client.put(f"/personas/{persona_id}/likes", headers=alice)
    client.put(f"/personas/{persona_id}/likes", headers=bob)

    assert _count(client, persona_id) == 2


def test_count_is_public(client: TestClient) -> None:
    # 방송국 페이지는 비로그인 시청자에게도 보여야 한다(FR-STATION-1).
    res = client.get(f"/personas/{uuid4()}/likes")

    assert res.status_code == 200
    assert res.json()["count"] == 0


def test_like_requires_auth(client: TestClient) -> None:
    assert client.put(f"/personas/{uuid4()}/likes").status_code == 401


def test_my_like_reflects_state(client: TestClient) -> None:
    headers = _auth_headers(client, "d@example.com")
    persona_id = str(uuid4())

    assert (
        client.get(f"/personas/{persona_id}/likes/me", headers=headers).status_code
        == 404
    )

    client.put(f"/personas/{persona_id}/likes", headers=headers)
    assert (
        client.get(f"/personas/{persona_id}/likes/me", headers=headers).status_code
        == 204
    )


def test_count_is_cached_then_invalidated(client: TestClient, redis: FakeRedis) -> None:
    headers = _auth_headers(client, "e@example.com")
    persona_id = str(uuid4())
    client.put(f"/personas/{persona_id}/likes", headers=headers)

    # 무효화됐으므로 아직 캐시가 없다.
    key = f"community:like_count:{persona_id}"
    assert redis.get(key) is None

    _count(client, persona_id)
    assert redis.get(key) == "1"  # 조회가 캐시를 채운다

    # 쓰기가 캐시를 지운다 — 본인 동작은 즉시 반영되어야 한다.
    client.delete(f"/personas/{persona_id}/likes", headers=headers)
    assert redis.get(key) is None
    assert _count(client, persona_id) == 0


def test_count_survives_redis_failure(client: TestClient, redis: FakeRedis) -> None:
    # 캐시는 성능 장치이지 정확성의 일부가 아니다. Redis가 죽어도 답은 나와야 한다.
    headers = _auth_headers(client, "f@example.com")
    persona_id = str(uuid4())
    client.put(f"/personas/{persona_id}/likes", headers=headers)

    redis.connected = False  # fakeredis: 이후 명령이 ConnectionError

    assert _count(client, persona_id) == 1


def test_public_profile_is_readable_by_anyone(client: TestClient) -> None:
    owner = _auth_headers(client, "owner@example.com")
    created = client.post(
        "/personas",
        json={"name": "홍세현", "tagline": "연애상담", "description": "따뜻한 조언"},
        headers=owner,
    ).json()

    # 인증 없이
    res = client.get(f"/personas/{created['id']}/profile")

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "홍세현"
    assert "owner_id" not in body  # 소유자는 공개면에 노출하지 않는다


def test_public_profile_missing_is_404(client: TestClient) -> None:
    res = client.get(f"/personas/{uuid4()}/profile")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "persona_not_found"


def test_management_get_still_requires_ownership(client: TestClient) -> None:
    # 공개 조회를 추가했다고 관리용 뷰가 열리면 안 된다.
    alice = _auth_headers(client, "alice2@example.com")
    bob = _auth_headers(client, "bob2@example.com")
    created = client.post(
        "/personas", json={"name": "내페르소나"}, headers=alice
    ).json()

    assert client.get(f"/personas/{created['id']}", headers=bob).status_code == 403
