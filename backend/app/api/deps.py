"""Общие зависимости обработчиков: сессия базы, текущий пользователь, проверка роли."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import utcnow
from app.core.security import hash_session_token
from app.models import User, UserSession

SESSION_COOKIE = "contract_review_session"
# Срок сессии продлевается не на каждый запрос, а не чаще раза в 5 минут.
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Обработчик сам делает commit; при исключении изменения откатываются."""
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
# Местные дата и время — по часам браузера: интерфейс передаёт смещение своего часового пояса от UTC.
UtcOffset = Annotated[int, Query(ge=-14 * 60, le=14 * 60, description="смещение от UTC в минутах")]


def find_session(db: Session, settings: Settings, token: str | None) -> UserSession | None:
    if not token:
        return None
    token_hash = hash_session_token(token, settings.app_secret_key)
    record = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    if record is None or record.expires_at <= utcnow():
        return None
    return record


def get_current_user(request: Request, db: DbSession, settings: AppSettings) -> User:
    record = find_session(db, settings, request.cookies.get(SESSION_COOKIE))
    if record is None or not record.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    now = utcnow()
    if now - record.last_seen_at >= SESSION_TOUCH_INTERVAL:
        record.last_seen_at = now
        record.expires_at = now + timedelta(hours=settings.session_ttl_hours)
        db.commit()
    return record.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Раздел доступен только администратору")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
