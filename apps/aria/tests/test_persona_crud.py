from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from aria.app import create_app
from aria.common.db import get_session


@pytest.fixture
def client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)  # identity_user + persona 등록됨
    app = create_app()

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers(client: TestClient, email: str) -> dict[str, str]:
    client.post(
        "/auth/register",
        json={
            "email": email,
            "username": email.split("@")[0] + "_user",  # min_length=2 보장
            "password": "s3cret-pw",
        },
    )
    token = client.post(
        "/auth/login", json={"email": email, "password": "s3cret-pw"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, headers: dict[str, str], **over: str) -> dict:
    body = {"name": "아리아", "tagline": "연애 상담", "description": "따뜻한 조언"}
    body.update(over)
    return client.post("/personas", json=body, headers=headers).json()


def test_create_persona_sets_owner_to_caller(client: TestClient) -> None:
    headers = _auth_headers(client, "a@aria.dev")
    me = client.get("/auth/me", headers=headers).json()

    resp = client.post("/personas", json={"name": "아리아"}, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "아리아"
    assert data["owner_id"] == me["id"]
    assert data["is_active"] is True


def test_create_requires_auth(client: TestClient) -> None:
    resp = client.post("/personas", json={"name": "아리아"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


def test_create_rejects_blank_name(client: TestClient) -> None:
    headers = _auth_headers(client, "a@aria.dev")
    resp = client.post("/personas", json={"name": ""}, headers=headers)
    assert resp.status_code == 422


def test_list_returns_only_my_personas(client: TestClient) -> None:
    alice = _auth_headers(client, "alice@aria.dev")
    bob = _auth_headers(client, "bob@aria.dev")
    _create(client, alice, name="앨리스봇")
    _create(client, bob, name="밥봇")

    mine = client.get("/personas", headers=alice).json()
    assert [p["name"] for p in mine] == ["앨리스봇"]


def test_get_others_persona_is_forbidden(client: TestClient) -> None:
    alice = _auth_headers(client, "alice@aria.dev")
    bob = _auth_headers(client, "bob@aria.dev")
    alice_persona = _create(client, alice)

    resp = client.get(f"/personas/{alice_persona['id']}", headers=bob)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_persona_owner"


def test_get_missing_persona_is_not_found(client: TestClient) -> None:
    headers = _auth_headers(client, "a@aria.dev")
    resp = client.get("/personas/00000000-0000-7000-8000-000000000000", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "persona_not_found"


def test_update_persona(client: TestClient) -> None:
    headers = _auth_headers(client, "a@aria.dev")
    persona = _create(client, headers)

    resp = client.patch(
        f"/personas/{persona['id']}",
        json={"tagline": "새 슬로건"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tagline"] == "새 슬로건"
    assert body["name"] == "아리아"  # 안 건드린 필드는 유지


def test_update_others_persona_is_forbidden(client: TestClient) -> None:
    alice = _auth_headers(client, "alice@aria.dev")
    bob = _auth_headers(client, "bob@aria.dev")
    persona = _create(client, alice)

    resp = client.patch(
        f"/personas/{persona['id']}", json={"name": "탈취"}, headers=bob
    )
    assert resp.status_code == 403


def test_delete_persona(client: TestClient) -> None:
    headers = _auth_headers(client, "a@aria.dev")
    persona = _create(client, headers)

    delete = client.delete(f"/personas/{persona['id']}", headers=headers)
    assert delete.status_code == 204

    after = client.get(f"/personas/{persona['id']}", headers=headers)
    assert after.status_code == 404
