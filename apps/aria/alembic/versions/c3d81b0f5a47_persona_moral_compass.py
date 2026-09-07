"""persona moral compass

Revision ID: c3d81b0f5a47
Revises: 7f4a1c9d2e10
Create Date: 2026-09-06 14:20:11.038214
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # SQLModel autogenerate가 AutoString 등을 참조하므로 필요
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d81b0f5a47"
down_revision: str | None = "7f4a1c9d2e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "persona_moral_compass",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("persona_id", sa.Uuid(), nullable=False),
        sa.Column(
            "standard", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False
        ),
        sa.Column(
            "rule_adherence",
            sqlmodel.sql.sqltypes.AutoString(length=200),
            nullable=False,
        ),
        sa.Column(
            "fairness", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False
        ),
        sa.PrimaryKeyConstraint("persona_id"),
    )


def downgrade() -> None:
    op.drop_table("persona_moral_compass")
