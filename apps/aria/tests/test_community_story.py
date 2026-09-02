"""community 사연 게시판 슬라이스 테스트.

인메모리 SQLite로 hermetic하게 돈다(인프라 불필요). persona 슬라이스 테스트와 같은 방식.
"""

from collections.abc import Iterator
from uuid import uuid4

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
    SQLModel.metadata.create_all(engine)
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
            "username": email.split("@")[0] + "_user",
            "password": "s3cret-pw",
        },
    )
    token = client.post(
        "/auth/login", json={"email": email, "password": "s3cret-pw"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _submit(client: TestClient, headers: dict[str, str], **over: object) -> dict:
    body: dict[str, object] = {
        "persona_id": str(uuid4()),
        "title": "3년 만난 사람과 헤어졌어요",
        "content": "아직도 연락하고 싶은데 참아야 할까요?",
    }
    body.update(over)
    return client.post("/stories", json=body, headers=headers).json()


def test_submit_story(client: TestClient) -> None:
    headers = _auth_headers(client, "a@example.com")
    persona_id = str(uuid4())

    res = client.post(
        "/stories",
        json={
            "persona_id": persona_id,
            "title": "썸 타는 중인데요",
            "content": "답장이 느려요",
            "relationship_stage": "썸",
            "nickname": "고민많은사람",
        },
        headers=headers,
    )

    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "썸 타는 중인데요"
    assert body["relationship_stage"] == "썸"
    assert body["status"] == "pending"  # 낭독 대기가 기본
    assert body["is_anonymous"] is True  # 민감한 상담이라 익명이 기본


def test_anonymous_story_hides_author(client: TestClient) -> None:
    headers = _auth_headers(client, "b@example.com")

    anon = _submit(client, headers)
    named = _submit(client, headers, is_anonymous=False)

    assert anon["author_id"] is None  # 익명 → 작성자 감춤
    assert named["author_id"] is not None


def test_submit_requires_auth(client: TestClient) -> None:
    res = client.post(
        "/stories",
        json={"persona_id": str(uuid4()), "title": "제목", "content": "내용"},
    )

    assert res.status_code == 401


def test_list_is_public_and_scoped_to_persona(client: TestClient) -> None:
    headers = _auth_headers(client, "c@example.com")
    mine, other = str(uuid4()), str(uuid4())
    _submit(client, headers, persona_id=mine, title="첫 사연")
    _submit(client, headers, persona_id=mine, title="둘째 사연")
    _submit(client, headers, persona_id=other, title="다른 방송국")

    # 인증 헤더 없이 — 열람은 공개다(FR-STATION-3)
    res = client.get("/stories", params={"persona_id": mine})

    assert res.status_code == 200
    titles = [s["title"] for s in res.json()]
    assert len(titles) == 2
    assert "다른 방송국" not in titles


def test_list_is_newest_first(client: TestClient) -> None:
    # 게시판이라 최신 사연이 위에 와야 한다. 복합 인덱스
    # (persona_id, created_at DESC)도 이 정렬을 전제로 만들어져 있다.
    headers = _auth_headers(client, "g@example.com")
    persona_id = str(uuid4())
    for i in range(3):
        _submit(client, headers, persona_id=persona_id, title=f"사연 {i}")

    titles = [
        s["title"]
        for s in client.get("/stories", params={"persona_id": persona_id}).json()
    ]

    assert titles == ["사연 2", "사연 1", "사연 0"]


def test_list_paginates(client: TestClient) -> None:
    headers = _auth_headers(client, "d@example.com")
    persona_id = str(uuid4())
    for i in range(3):
        _submit(client, headers, persona_id=persona_id, title=f"사연 {i}")

    page = client.get("/stories", params={"persona_id": persona_id, "limit": 2}).json()
    rest = client.get(
        "/stories", params={"persona_id": persona_id, "limit": 2, "offset": 2}
    ).json()

    assert len(page) == 2
    assert len(rest) == 1
    # 페이지가 겹치거나 빠뜨리지 않는다.
    assert [s["title"] for s in page] + [s["title"] for s in rest] == [
        "사연 2",
        "사연 1",
        "사연 0",
    ]


def test_list_rejects_oversized_limit(client: TestClient) -> None:
    # 무한정 긁어가는 것을 막는다.
    res = client.get("/stories", params={"persona_id": str(uuid4()), "limit": 1000})

    assert res.status_code == 422


def test_get_story(client: TestClient) -> None:
    headers = _auth_headers(client, "e@example.com")
    created = _submit(client, headers)

    res = client.get(f"/stories/{created['id']}")

    assert res.status_code == 200
    assert res.json()["id"] == created["id"]


def test_get_missing_story_is_404(client: TestClient) -> None:
    res = client.get(f"/stories/{uuid4()}")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "story_not_found"


def test_title_is_validated(client: TestClient) -> None:
    headers = _auth_headers(client, "f@example.com")

    res = client.post(
        "/stories",
        json={"persona_id": str(uuid4()), "title": "", "content": "내용"},
        headers=headers,
    )

    assert res.status_code == 422
