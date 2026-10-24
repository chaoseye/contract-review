"""Подключения ИИ

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "llm_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("params", JSON_TYPE, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('openai_compat', 'codex_cli')", name=op.f("ck_llm_profiles_kind_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_profiles")),
        sa.UniqueConstraint("name", name=op.f("uq_llm_profiles_name")),
    )
    # Активным может быть только один профиль.
    op.create_index(
        "uq_llm_profiles_single_active",
        "llm_profiles",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active"),
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_profiles_single_active", table_name="llm_profiles")
    op.drop_table("llm_profiles")
