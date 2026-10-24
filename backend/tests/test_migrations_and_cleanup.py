import io
from datetime import timedelta

from alembic import command
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.cli import alembic_config
from app.core.db import make_engine, utcnow
from app.jobs.cleanup import delete_expired_sessions
from app.models import Base, UserSession
from tests.conftest import add_user


def test_migrations_match_models(settings) -> None:
    engine = make_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert tables == set(Base.metadata.tables)
        for table in Base.metadata.sorted_tables:
            db_columns = {column["name"] for column in inspector.get_columns(table.name)}
            assert db_columns == set(table.columns.keys()), table.name
    finally:
        engine.dispose()


def test_migrate_creates_missing_sqlite_directory(tmp_path) -> None:
    """На чистой машине папки для локальной базы ещё нет — миграция должна её создать."""
    database = tmp_path / "not" / "yet" / "dev.db"
    config = alembic_config(f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    assert database.is_file()


def test_downgrade_to_base_and_back(settings) -> None:
    config = alembic_config(settings.database_url)
    config.attributes["configure_logging"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_postgresql_sql_is_generated() -> None:
    """PostgreSQL локально нет: проверяем, что миграции компилируются в SQL для него (офлайн-режим)."""
    buffer = io.StringIO()
    config = alembic_config("postgresql+pg8000://user:pass@localhost/contract_review")
    config.output_buffer = buffer
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE sessions" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "ON DELETE CASCADE" in sql
    # Только один активный профиль подключения ИИ: частичный уникальный индекс.
    index = "CREATE UNIQUE INDEX uq_llm_profiles_single_active"
    assert f"{index} ON llm_profiles (is_active) WHERE is_active" in sql
    # 0007: тип подключения anthropic допускается ограничением.
    kinds = "CHECK (kind IN ('openai_compat', 'codex_cli', 'anthropic'))"
    assert f"ALTER TABLE llm_profiles ADD CONSTRAINT ck_llm_profiles_kind_valid {kinds}" in sql


def test_cleanup_removes_only_expired_sessions(db_factory: sessionmaker[Session]) -> None:
    user_id = add_user(db_factory)
    now = utcnow()
    with db_factory() as db:
        db.add_all(
            [
                UserSession(user_id=user_id, token_hash="a" * 64, expires_at=now - timedelta(minutes=1)),
                UserSession(user_id=user_id, token_hash="b" * 64, expires_at=now + timedelta(hours=1)),
            ]
        )
        db.commit()
    with db_factory() as db:
        assert delete_expired_sessions(db) == 1
        db.commit()
    with db_factory() as db:
        assert db.scalars(select(UserSession.token_hash)).all() == ["b" * 64]
