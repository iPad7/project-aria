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
    SQLModel.metadata.create_all(engine)  # identity_user 등록됨(create_app import 경유)

    app = create_app()

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, raise_server_exceptions=False)


def _register(client: TestClient, **over: str) -> object:
    body = {"email": "a@aria.dev", "username": "aria", "password": "s3cret-pw"}
    body.update(over)
    return client.post("/auth/register", json=body)


def test_register_returns_user_without_password(client: TestClient) -> None:
    resp = _register(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "a@aria.dev"
    assert data["username"] == "aria"
    assert data["is_active"] is True
    assert "id" in data
    # 비밀번호/해시는 응답 어디에도 없어야 한다
    assert "password" not in data
    assert "password_hash" not in data


def test_register_duplicate_email_conflicts(client: TestClient) -> None:
    _register(client)
    resp = _register(client, username="other")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


def test_register_rejects_weak_password(client: TestClient) -> None:
    resp = _register(client, password="short")
    assert resp.status_code == 422  # DTO 검증(min_length=8)


def test_login_wrong_password_unauthorized(client: TestClient) -> None:
    _register(client)
    resp = client.post(
        "/auth/login", json={"email": "a@aria.dev", "password": "wrong-pw"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_unauthorized(client: TestClient) -> None:
    # 존재하지 않는 이메일도 동일한 코드/상태 (사용자 열거 방지)
    resp = client.post(
        "/auth/login", json={"email": "ghost@aria.dev", "password": "whatever-pw"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


def test_login_success_returns_token(client: TestClient) -> None:
    _register(client)
    resp = client.post(
        "/auth/login", json={"email": "a@aria.dev", "password": "s3cret-pw"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_me_requires_auth(client: TestClient) -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


def test_me_rejects_garbage_token(client: TestClient) -> None:
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_me_returns_current_user(client: TestClient) -> None:
    _register(client)
    token = client.post(
        "/auth/login", json={"email": "a@aria.dev", "password": "s3cret-pw"}
    ).json()["access_token"]

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@aria.dev"
    assert "password_hash" not in resp.json()
