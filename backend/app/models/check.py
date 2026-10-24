"""Проверка договора и очередь фоновых задач (documentation/ARCHITECTURE.md, разделы 2, 9, 10)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import UTCDateTime, utcnow
from app.models.base import Base
from app.models.user import User

# В PostgreSQL — JSONB, в SQLite (разработка и тесты) — обычный JSON.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class CheckStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"


class JobKind(StrEnum):
    EXTRACT = "extract"
    ANALYZE = "analyze"


class Procurement(StrEnum):
    """Режим закупки, который менеджер указывает при загрузке."""

    COMMERCIAL = "commercial"
    FZ223 = "223fz"
    FZ44 = "44fz"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def new_check_id() -> str:
    # Случайный идентификатор: по адресу проверки нельзя перебрать чужие.
    return uuid.uuid4().hex


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_check_id)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=CheckStatus.UPLOADED)
    # Что сейчас происходит, для показа менеджеру: «Распознавание страницы 3 из 13».
    progress: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_kind: Mapped[str] = mapped_column(String(8))
    file_size: Mapped[int] = mapped_column(BigInteger)
    comment: Mapped[str] = mapped_column(Text, default="")
    procurement: Mapped[str] = mapped_column(
        String(16), default=Procurement.UNKNOWN, server_default="unknown"
    )
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    warnings: Mapped[list[str] | None] = mapped_column(JsonType)
    # Сообщение об ошибке для менеджера; без фрагментов договора.
    error: Mapped[str | None] = mapped_column(Text)
    # Ответ модели и результаты его проверки (app/analysis/validate.py, CheckedAnswer.to_dict).
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    # Сведения об анализе: подключение, модель, токены, стоимость, отпечаток промпта, ошибка провайдера.
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    # Данные и строки протокола, которые правит менеджер (app/protocol/editing.py, ProtocolState).
    protocol: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    last_activity_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)

    owner: Mapped[User] = relationship()
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="check", cascade="all, delete-orphan", passive_deletes=True
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    check_id: Mapped[str] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Пока задача выполняется, worker обновляет locked_at; зависшие задачи возвращаются в очередь.
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_by: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    check: Mapped[Check] = relationship(back_populates="jobs")
