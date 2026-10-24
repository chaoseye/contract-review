"""Настройки, которые меняет администратор: таблица settings.

Срок жизни проверки и лимит файла по умолчанию берутся из .env (CHECK_TTL_HOURS, MAX_UPLOAD_MB), пока
администратор их не поменял. Шаблон протокола по умолчанию — тексты из templates.py.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import SettingEntry
from app.protocol.templates import ProtocolTemplate

LIMITS_KEY = "limits"
TEMPLATE_KEY = "protocol_template"


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_ttl_hours: int = Field(ge=1, le=24 * 30)
    max_upload_mb: int = Field(ge=1, le=200)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _value(db: Session, key: str) -> Any:
    entry = db.get(SettingEntry, key)
    return entry.value if entry is not None else None


def _put(db: Session, key: str, value: Any, user_id: int | None) -> None:
    entry = db.get(SettingEntry, key)
    if entry is None:
        db.add(SettingEntry(key=key, value=value, updated_by=user_id))
    else:
        entry.value = value
        entry.updated_by = user_id
    db.flush()


def get_limits(db: Session, settings: Settings) -> Limits:
    defaults = {"check_ttl_hours": settings.check_ttl_hours, "max_upload_mb": settings.max_upload_mb}
    stored = _value(db, LIMITS_KEY)
    if isinstance(stored, dict):
        try:
            return Limits.model_validate({**defaults, **stored})
        except ValidationError:
            pass  # испорченное значение не должно останавливать работу: действуют значения из .env
    return Limits.model_validate(defaults)


def set_limits(db: Session, limits: Limits, user_id: int | None) -> None:
    _put(db, LIMITS_KEY, limits.model_dump(), user_id)


def get_template(db: Session) -> ProtocolTemplate:
    stored = _value(db, TEMPLATE_KEY)
    if isinstance(stored, dict):
        try:
            return ProtocolTemplate.model_validate(stored)
        except ValidationError:
            pass
    return ProtocolTemplate()


def set_template(db: Session, template: ProtocolTemplate, user_id: int | None) -> None:
    _put(db, TEMPLATE_KEY, template.model_dump(), user_id)
