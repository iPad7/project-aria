"""Alembic 환경.

접속 URL은 앱 설정(config.Settings, ARIA_DATABASE_URL)에서 끌어와 단일 소스를 유지한다.
autogenerate가 스키마를 인식하려면 모든 SQLModel 테이블 모델을 import해 metadata에
등록해야 한다 — env는 마이그레이션 진입점이라 합성 루트처럼 여러 컨텍스트를 알아도 된다
(aria 패키지 밖이라 import-linter 계약과 무관).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from aria.common.config import settings

# 테이블 모델 등록 (metadata 채우기). 새 테이블이 생기면 여기에 import 추가.
from aria.contexts.chat.adapter.outbound.persistence import (  # noqa: F401
    model as _chat_model,
)
from aria.contexts.community.adapter.outbound.persistence import (
    model as _community,  # noqa: F401
)
from aria.contexts.identity.adapter.outbound.persistence import (
    model as _identity,  # noqa: F401
)
from aria.contexts.persona.adapter.outbound.persistence import (
    model as _persona,  # noqa: F401
)
from aria.contexts.wallet.adapter.outbound.persistence import (
    model as _wallet,  # noqa: F401
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
