"""Общие фикстуры. Каждый тест получает свою SQLite-базу с применёнными миграциями.

Настройки создаются явно и без .env (_env_file=None), чтобы локальный .env разработчика не влиял на тесты.
Переменные окружения с теми же именами убираются по той же причине: в контейнере их задаёт compose
(ENCRYPTION_KEY, CHECK_TTL_HOURS и другие настройки рабочей системы).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.middleware import CSRF_HEADER, CSRF_HEADER_VALUE
from app.cli import alembic_config
from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.core.security import hash_password
from app.main import create_app
from app.models import User, UserRole

# Имена переменных pydantic-settings сравнивает без учёта регистра.
for _name in [name for name in os.environ if name.lower() in Settings.model_fields]:
    del os.environ[_name]

TEST_SECRET = "test-secret-key-0123456789-abcdefghijklmnop"
PASSWORD = "correct-horse-battery"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "app_secret_key": TEST_SECRET,
        "data_dir": tmp_path / "data",
        "static_dir": tmp_path / "no-static",
        "public_url": "http://localhost:8080",
        "initial_admin_login": "",
        "initial_admin_password": "",
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def migrate(settings: Settings) -> None:
    config = alembic_config(settings.database_url)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    migrate(settings)
    return settings


@pytest.fixture
def db_factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(settings.database_url)
    yield make_sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        test_client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        yield test_client


def add_user(
    factory: sessionmaker[Session],
    login: str = "manager",
    role: UserRole = UserRole.MANAGER,
    password: str = PASSWORD,
    is_active: bool = True,
) -> int:
    with factory() as db:
        user = User(
            login=login,
            name=login.title(),
            role=role,
            password_hash=hash_password(password),
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        return user.id
