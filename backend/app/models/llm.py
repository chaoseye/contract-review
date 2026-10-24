"""Подключения ИИ (documentation/ARCHITECTURE.md, разделы 5 и 9)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import UTCDateTime, utcnow
from app.models.base import Base
from app.models.check import JsonType


class LlmKind(StrEnum):
    OPENAI_COMPAT = "openai_compat"
    ANTHROPIC = "anthropic"
    CODEX_CLI = "codex_cli"


class LlmProfile(Base):
    __tablename__ = "llm_profiles"
    __table_args__ = (
        # Миграция 0007 добавила anthropic.
        CheckConstraint("kind IN ('openai_compat', 'codex_cli', 'anthropic')", name="kind_valid"),
        # Активным может быть только один профиль: частичный уникальный индекс по строкам с is_active.
        Index(
            "uq_llm_profiles_single_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    # Ключ API, зашифрованный ENCRYPTION_KEY (app/core/crypto.py). Пусто — ключ не нужен (Ollama, Codex).
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(200), default="")
    # Параметры запроса и сведения о модели: app/analysis/profiles.py, ProfileParams.
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
