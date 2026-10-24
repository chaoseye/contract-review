"""Подключение к API Anthropic: новый тип в llm_profiles.kind

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "kind IN ('openai_compat', 'codex_cli')"
NEW_KINDS = "kind IN ('openai_compat', 'codex_cli', 'anthropic')"


def _replace_kind_check(condition: str) -> None:
    # SQLite не меняет ограничения на месте — batch пересоздаёт таблицу; PostgreSQL получает ALTER TABLE.
    with op.batch_alter_table("llm_profiles") as batch:
        batch.drop_constraint(op.f("ck_llm_profiles_kind_valid"), type_="check")
        batch.create_check_constraint(op.f("ck_llm_profiles_kind_valid"), condition)


def upgrade() -> None:
    _replace_kind_check(NEW_KINDS)


def downgrade() -> None:
    if not context.is_offline_mode():
        count = op.get_bind().scalar(sa.text("SELECT count(*) FROM llm_profiles WHERE kind = 'anthropic'"))
        if count:
            raise RuntimeError(
                f"Подключений Anthropic: {count}. Прежняя схема их не допускает — удалите их перед откатом."
            )
    _replace_kind_check(OLD_KINDS)
