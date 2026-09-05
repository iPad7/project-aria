"""room closed_at

Revision ID: 7f4a1c9d2e10
Revises: 20bceff0690e
Create Date: 2026-09-05 10:12:04.118273
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f4a1c9d2e10"
down_revision: str | None = "20bceff0690e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 이미 끝난 방은 종료 시각을 모른다 — nullable로 둔다. 소급해 채우면(예: updated_at)
    # 실제로 방송이 끝난 시각이 아닌 값이 기록으로 남는다.
    op.add_column(
        "chat_room",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_room", "closed_at")
