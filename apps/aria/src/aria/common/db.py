"""데이터베이스 엔진과 세션 (횡단 인프라).

엔진은 모듈 로드 시 만들지만 실제 연결은 지연되므로, DB 없이도 import·/health는 뜬다.
세션은 FastAPI 의존성 `get_session`으로 요청 스코프에 주입한다.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from aria.common.config import settings

engine = create_engine(settings.database_url, echo=False)


def create_db_and_tables() -> None:
    """등록된 SQLModel 테이블을 생성. 개발/테스트용 (운영은 Alembic — 후속)."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
