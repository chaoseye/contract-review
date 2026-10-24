"""Организация, журнал действий и протокол в проверке

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BINARY = sa.LargeBinary().with_variant(postgresql.BYTEA(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=500), nullable=False),
        sa.Column("short_name", sa.String(length=300), nullable=False),
        sa.Column("signer", sa.String(length=200), nullable=False),
        sa.Column("representative", sa.String(length=300), nullable=False),
        sa.Column("basis", sa.String(length=300), nullable=False),
        sa.Column("default_city", sa.String(length=100), nullable=False),
        sa.Column("requisites", JSON_TYPE, nullable=False),
        sa.Column("footer_text", sa.Text(), nullable=False),
        sa.Column("signature_png", BINARY, nullable=True),
        sa.Column("stamp_png", BINARY, nullable=True),
        sa.Column("header_png", BINARY, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("check_id", sa.String(length=32), nullable=True),
        sa.Column("counterparty", sa.String(length=500), nullable=True),
        sa.Column("contract_number", sa.String(length=100), nullable=True),
        sa.Column("with_signature", sa.Boolean(), nullable=True),
        sa.Column("with_stamp", sa.Boolean(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_rub", sa.Float(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audit_log_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"])
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"])
    op.create_index(op.f("ix_audit_log_check_id"), "audit_log", ["check_id"])
    op.add_column("checks", sa.Column("protocol", JSON_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("checks", "protocol")
    op.drop_index(op.f("ix_audit_log_check_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("organizations")
