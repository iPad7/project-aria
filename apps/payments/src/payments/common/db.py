"""데이터베이스 엔진과 세션.

엔진은 모듈 로드 시 만들지만 실제 연결은 지연되므로, DB 없이도 import·/health는 뜬다.
스키마는 Alembic 마이그레이션이 단일 소스다(`apps/payments/alembic`).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, create_engine

from payments.common.config import settings

engine = create_engine(settings.database_url, echo=False)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
