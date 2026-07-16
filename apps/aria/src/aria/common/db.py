"""데이터베이스 엔진과 세션 (횡단 인프라).

엔진은 모듈 로드 시 만들지만 실제 연결은 지연되므로, DB 없이도 import·/health는 뜬다.
세션은 FastAPI 의존성 `get_session`으로 요청 스코프에 주입한다.

스키마는 Alembic 마이그레이션이 단일 소스다(`apps/aria/alembic`). 앱은 테이블을
직접 생성하지 않는다 — `uv run alembic upgrade head`로 반영한다. 테스트는 인메모리
SQLite에 `SQLModel.metadata.create_all`을 직접 쓴다(마이그레이션 경로 밖).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, create_engine

from aria.common.config import settings

engine = create_engine(settings.database_url, echo=False)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
