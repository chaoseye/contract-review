"""Версии промпта, настройки, подробности в журнале

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(length=12), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_prompt_versions_author_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_versions")),
    )
    op.create_index(
        "uq_prompt_versions_single_active",
        "prompt_versions",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active"),
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", JSON_TYPE, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_settings_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    op.add_column("audit_log", sa.Column("details", sa.String(length=500), nullable=True))
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    # SQLite не умеет удалять столбцы без пересоздания таблицы — batch делает это сам.
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_column("details")
    op.drop_table("settings")
    op.drop_index("uq_prompt_versions_single_active", table_name="prompt_versions")
    op.drop_table("prompt_versions")
