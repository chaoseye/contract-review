"""Результат анализа в проверке и режим закупки

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "checks", sa.Column("procurement", sa.String(length=16), server_default="unknown", nullable=False)
    )
    op.add_column("checks", sa.Column("result", JSON_TYPE, nullable=True))
    op.add_column("checks", sa.Column("analysis", JSON_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("checks", "analysis")
    op.drop_column("checks", "result")
    op.drop_column("checks", "procurement")
