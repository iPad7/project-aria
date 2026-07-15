from starlette.testclient import TestClient

from aria.app import app


def test_health() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}
