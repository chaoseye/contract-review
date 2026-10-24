"""Версии промпта и настройки системы (documentation/ARCHITECTURE.md, раздел 9)."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import UTCDateTime, utcnow
from app.models.base import Base
from app.models.check import JsonType


class PromptVersion(Base):
    """Версия промпта администратора. Активна одна; откат — активация прежней версии."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        Index(
            "uq_prompt_versions_single_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    comment: Mapped[str] = mapped_column(String(500), default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Первые 12 знаков SHA-256 текста: им помечаются анализы и записи журнала.
    fingerprint: Mapped[str] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())


class SettingEntry(Base):
    """Настройка «ключ — значение (JSON)»: срок жизни проверки, лимит файла, шаблон протокола."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
