"""Наша организация и журнал действий (documentation/ARCHITECTURE.md, раздел 9)."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import UTCDateTime, utcnow
from app.models.base import Base
from app.models.check import JsonType


class Organization(Base):
    """Поставщик: наименование и подписант для протокола, реквизиты для сверки, подпись, печать, бланк.

    Сейчас организация одна. Данные вносит администратор в разделе «Организация» или команда
    `python -m app.cli org-set`.
    """

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Как во вступлении протокола: «Индивидуальный предприниматель Иванов Иван Иванович».
    full_name: Mapped[str] = mapped_column(String(500), default="")
    # Как в блоке подписей: «ИП Иванов Иван Иванович».
    short_name: Mapped[str] = mapped_column(String(300), default="")
    # Расшифровка подписи: «И.И. Иванов».
    signer: Mapped[str] = mapped_column(String(200), default="")
    # Для юрлица: «в лице генерального директора …», «действующего на основании Устава»; у ИП пусто.
    representative: Mapped[str] = mapped_column(String(300), default="")
    basis: Mapped[str] = mapped_column(String(300), default="")
    # Город протокола, если в договоре его нет.
    default_city: Mapped[str] = mapped_column(String(100), default="")
    # ИНН, ОГРНИП, адреса, банк, e-mail, телефон — для сверки реквизитов.
    requisites: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    footer_text: Mapped[str] = mapped_column(Text, default="")
    signature_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    stamp_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    header_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    @property
    def ready_for_protocol(self) -> bool:
        # Нужно только полное наименование: без краткого в подписях стоит полное, без подписанта — линия.
        return bool(self.full_name.strip())


class AuditEntry(Base):
    """Журнал: кто и когда работал, с подписью или без, модель, токены, стоимость. Без текстов."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # upload, analysis_done, analysis_failed, protocol_docx, protocol_pdf, delete; действия администратора —
    # app/audit.py, ACTION_LABELS.
    action: Mapped[str] = mapped_column(String(32), index=True)
    # Без внешнего ключа: запись остаётся, когда проверку удаляют по сроку.
    check_id: Mapped[str | None] = mapped_column(String(32), index=True)
    counterparty: Mapped[str | None] = mapped_column(String(500))
    contract_number: Mapped[str | None] = mapped_column(String(100))
    with_signature: Mapped[bool | None] = mapped_column(Boolean)
    with_stamp: Mapped[bool | None] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(200))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_rub: Mapped[float | None] = mapped_column(Float)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    # Что изменил администратор: «пользователь ivanov: роль manager → admin». Без текстов договоров.
    details: Mapped[str | None] = mapped_column(String(500))
