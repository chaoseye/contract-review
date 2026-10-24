import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import ensure_initial_admin
from app.core.security import verify_password
from app.models import User
from tests.conftest import add_user, make_settings


def test_creates_admin_when_no_users(tmp_path, db_factory: sessionmaker[Session]) -> None:
    settings = make_settings(tmp_path, initial_admin_login=" Admin ", initial_admin_password="long-enough-1")
    with db_factory() as db:
        created = ensure_initial_admin(db, settings)
        db.commit()
    assert created is not None
    with db_factory() as db:
        user = db.scalars(select(User)).one()
    assert user.login == "admin"
    assert user.role == "admin"
    assert verify_password(user.password_hash, "long-enough-1")


def test_is_idempotent(tmp_path, db_factory: sessionmaker[Session]) -> None:
    settings = make_settings(tmp_path, initial_admin_login="admin", initial_admin_password="long-enough-1")
    for _ in range(2):
        with db_factory() as db:
            ensure_initial_admin(db, settings)
            db.commit()
    with db_factory() as db:
        assert len(db.scalars(select(User)).all()) == 1


def test_does_nothing_when_users_exist(tmp_path, db_factory: sessionmaker[Session]) -> None:
    add_user(db_factory, login="someone")
    settings = make_settings(tmp_path, initial_admin_login="admin", initial_admin_password="long-enough-1")
    with db_factory() as db:
        assert ensure_initial_admin(db, settings) is None


def test_does_nothing_without_credentials(tmp_path, db_factory: sessionmaker[Session]) -> None:
    with db_factory() as db:
        assert ensure_initial_admin(db, make_settings(tmp_path)) is None
        assert db.scalars(select(User)).all() == []


def test_rejects_short_password(tmp_path, db_factory: sessionmaker[Session]) -> None:
    settings = make_settings(tmp_path, initial_admin_login="admin", initial_admin_password="short")
    with db_factory() as db, pytest.raises(ValueError, match="не короче"):
        ensure_initial_admin(db, settings)
