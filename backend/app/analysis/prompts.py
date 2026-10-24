"""Версии промпта администратора: редактор, откат, отпечатки для журнала.

Первая версия — стартовый промпт из default_prompt.py; она создаётся сама, если версий ещё нет.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analysis.default_prompt import DEFAULT_ADMIN_PROMPT
from app.analysis.prompt import prompt_fingerprint
from app.models import PromptVersion

FIRST_COMMENT = "Первая версия: стартовый промпт и позиции поставщика по умолчанию"
MAX_PROMPT_LENGTH = 60_000


class PromptError(ValueError):
    """Неверная версия промпта. Текст — для администратора."""


def list_versions(db: Session) -> list[PromptVersion]:
    active_version(db)
    return list(db.scalars(select(PromptVersion).order_by(PromptVersion.id.desc())))


def active_version(db: Session) -> PromptVersion:
    """Активная версия. Если версий нет — создаётся первая; если активной нет — активируется последняя."""
    version = db.scalar(select(PromptVersion).where(PromptVersion.is_active.is_(True)))
    if version is not None:
        return version
    latest = db.scalar(select(PromptVersion).order_by(PromptVersion.id.desc()).limit(1))
    if latest is None:
        latest = PromptVersion(
            text=DEFAULT_ADMIN_PROMPT,
            comment=FIRST_COMMENT,
            author_id=None,
            fingerprint=prompt_fingerprint(DEFAULT_ADMIN_PROMPT),
            is_active=False,
        )
        db.add(latest)
        db.flush()
    activate_version(db, latest)
    return latest


def create_version(
    db: Session, text: str, comment: str, author_id: int | None, activate: bool = True
) -> PromptVersion:
    text = text.strip()
    if not text:
        raise PromptError("Промпт пустой.")
    if len(text) > MAX_PROMPT_LENGTH:
        raise PromptError(f"Промпт длиннее {MAX_PROMPT_LENGTH} символов.")
    current = active_version(db)
    if text == current.text:
        raise PromptError("Текст не отличается от активной версии.")
    version = PromptVersion(
        text=text,
        comment=comment.strip()[:500],
        author_id=author_id,
        fingerprint=prompt_fingerprint(text),
        is_active=False,
    )
    db.add(version)
    db.flush()
    if activate:
        activate_version(db, version)
    return version


def activate_version(db: Session, version: PromptVersion) -> None:
    # Сначала снимаем признак с остальных: уникальный индекс не допускает двух активных версий.
    db.execute(update(PromptVersion).where(PromptVersion.id != version.id).values(is_active=False))
    db.flush()
    version.is_active = True
    db.flush()
