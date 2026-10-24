"""Первый запуск: создание первого администратора из INITIAL_ADMIN_LOGIN / INITIAL_ADMIN_PASSWORD."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, validate_new_password
from app.models import User, UserRole
from app.models.user import normalize_login

log = logging.getLogger(__name__)


def ensure_initial_admin(db: Session, settings: Settings) -> User | None:
    """Создаёт администратора, только если пользователей ещё нет. Повторный вызов ничего не меняет."""
    if db.scalar(select(func.count()).select_from(User)):
        return None
    login = normalize_login(settings.initial_admin_login)
    password = settings.initial_admin_password
    if not login or not password:
        log.warning("Пользователей нет, а INITIAL_ADMIN_LOGIN и INITIAL_ADMIN_PASSWORD не заданы")
        return None
    validate_new_password(password)
    user = User(
        login=login,
        # Без имени интерфейс показывает логин; роль видна отдельной меткой.
        name="",
        role=UserRole.ADMIN,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.flush()
    log.info("Создан первый администратор: %s", login)
    return user
