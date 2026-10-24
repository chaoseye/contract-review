"""Вход, выход и текущий пользователь."""

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select

from app.api.deps import SESSION_COOKIE, AppSettings, CurrentUser, DbSession, find_session
from app.api.ratelimit import LoginLimiter
from app.api.schemas import LoginIn, UserOut
from app.core.db import utcnow
from app.core.security import (
    burn_password_check,
    hash_password,
    hash_session_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.models import User, UserSession
from app.models.user import normalize_login

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

INVALID_CREDENTIALS = "Неверный логин или пароль"


@router.post("/auth/login", response_model=UserOut)
def login(
    payload: LoginIn, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> User:
    limiter: LoginLimiter = request.app.state.login_limiter
    login_name = normalize_login(payload.login)
    address = request.client.host if request.client else "unknown"
    wait = limiter.retry_after(login_name, address)
    if wait is not None:
        minutes = max(1, -(-wait // 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Слишком много неудачных попыток входа. Повторите через {minutes} мин.",
            headers={"Retry-After": str(wait)},
        )
    user = db.scalar(select(User).where(User.login == login_name))
    if user is None:
        burn_password_check(payload.password)
        limiter.failed(login_name, address)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    if not verify_password(user.password_hash, payload.password):
        limiter.failed(login_name, address)
        log.info("неудачный вход: пользователь %s", user.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    limiter.succeeded(login_name)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись заблокирована")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    now = utcnow()
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at <= now))
    token = new_session_token()
    ttl = timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token, settings.app_secret_key),
            created_at=now,
            last_seen_at=now,
            expires_at=now + ttl,
        )
    )
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    log.info("вход: пользователь %s", user.id)
    return user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbSession, settings: AppSettings) -> None:
    record = find_session(db, settings, request.cookies.get(SESSION_COOKIE))
    if record is not None:
        log.info("выход: пользователь %s", record.user_id)
        db.delete(record)
        db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.secure_cookies, samesite="lax"
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
