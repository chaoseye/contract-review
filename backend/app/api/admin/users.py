"""Пользователи: создание, имя и роль, блокировка, сброс пароля (documentation/ARCHITECTURE.md, раздел 7.2).

Администратор не может заблокировать себя или снять с себя роль: иначе можно остаться без администратора.
По той же причине должен остаться хотя бы один активный администратор.
"""

import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import SESSION_COOKIE, AdminUser, AppSettings, DbSession
from app.core.security import hash_password, hash_session_token, validate_new_password
from app.models import User, UserRole, UserSession
from app.models.user import normalize_login

router = APIRouter(prefix="/users")

LOGIN_PATTERN = re.compile(r"^[\w.@-]{2,64}$")
ROLE_NAMES = {UserRole.ADMIN: "администратор", UserRole.MANAGER: "менеджер"}


class AdminUserOut(BaseModel):
    id: int
    login: str
    name: str
    role: str
    is_active: bool
    created_at: datetime
    # Последняя активность по действующим сессиям; None — сейчас не входил.
    last_seen_at: datetime | None


class UserCreate(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    role: UserRole = UserRole.MANAGER
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _out(db: Session, users: list[User]) -> list[AdminUserOut]:
    seen = dict(
        db.execute(
            select(UserSession.user_id, func.max(UserSession.last_seen_at)).group_by(UserSession.user_id)
        ).all()
    )
    return [
        AdminUserOut(
            id=user.id,
            login=user.login,
            name=user.name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_seen_at=seen.get(user.id),
        )
        for user in users
    ]


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    return user


def _other_active_admins(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != user_id
            )
        )
        or 0
    )


def _password(password: str) -> str:
    try:
        validate_new_password(password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return hash_password(password)


@router.get("", response_model=list[AdminUserOut])
def list_users(db: DbSession, _admin: AdminUser) -> list[AdminUserOut]:
    return _out(db, list(db.scalars(select(User).order_by(User.login))))


@router.post("", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: DbSession, admin: AdminUser) -> AdminUserOut:
    login = normalize_login(payload.login)
    if not LOGIN_PATTERN.match(login):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Логин — от 2 до 64 символов: буквы, цифры, точка, дефис, подчёркивание и @.",
        )
    if db.scalar(select(User.id).where(User.login == login)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Пользователь {login} уже есть")
    user = User(
        login=login,
        name=payload.name.strip(),
        role=payload.role,
        password_hash=_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.flush()
    audit.record(db, "user_create", user_id=admin.id, details=f"{login}, роль: {ROLE_NAMES[payload.role]}")
    db.commit()
    return _out(db, [user])[0]


@router.patch("/{user_id}", response_model=AdminUserOut)
def update_user(user_id: int, payload: UserUpdate, db: DbSession, admin: AdminUser) -> AdminUserOut:
    user = _get(db, user_id)
    changes: list[str] = []
    demoted = payload.role is not None and payload.role != user.role and user.is_admin
    blocked = payload.is_active is False and user.is_active
    if user.id == admin.id and (demoted or blocked):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Нельзя заблокировать себя или снять с себя роль администратора."
        )
    if (demoted or blocked) and user.is_admin and user.is_active and _other_active_admins(db, user.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Должен остаться хотя бы один активный администратор.")
    if payload.name is not None and payload.name.strip() != user.name:
        user.name = payload.name.strip()
        changes.append("имя")
    if payload.role is not None and payload.role != user.role:
        changes.append(f"роль: {ROLE_NAMES[UserRole(user.role)]} → {ROLE_NAMES[payload.role]}")
        user.role = payload.role
    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        changes.append("разблокирован" if payload.is_active else "заблокирован")
        if not payload.is_active:
            # Блокировка сразу завершает все сессии пользователя.
            db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    if changes:
        audit.record(db, "user_update", user_id=admin.id, details=f"{user.login}: {'; '.join(changes)}")
    db.commit()
    return _out(db, [user])[0]


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int,
    payload: PasswordIn,
    request: Request,
    db: DbSession,
    admin: AdminUser,
    settings: AppSettings,
) -> None:
    """Новый пароль завершает сессии пользователя. Свою текущую сессию администратор сохраняет."""
    user = _get(db, user_id)
    user.password_hash = _password(payload.password)
    query = delete(UserSession).where(UserSession.user_id == user.id)
    token = request.cookies.get(SESSION_COOKIE)
    if user.id == admin.id and token:
        query = query.where(UserSession.token_hash != hash_session_token(token, settings.app_secret_key))
    db.execute(query)
    audit.record(db, "user_password", user_id=admin.id, details=user.login)
    db.commit()
