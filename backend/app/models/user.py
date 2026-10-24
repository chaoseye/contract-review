from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import UTCDateTime, utcnow
from app.models.base import Base


class UserRole(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'manager')", name="role_valid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Логин хранится в нижнем регистре, см. normalize_login.
    login: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # HMAC токена из cookie; сам токен не хранится.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)

    user: Mapped[User] = relationship(back_populates="sessions")


def normalize_login(login: str) -> str:
    return login.strip().lower()
