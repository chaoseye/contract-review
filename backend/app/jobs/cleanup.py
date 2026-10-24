"""Периодическая очистка: просроченные сессии и проверки вместе с их файлами, а также исходные файлы,
которые уже не будут прочитаны."""

import logging
import shutil
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import storage
from app.core.config import Settings
from app.core.db import utcnow
from app.jobs import queue
from app.models import Check, JobKind, UserSession

log = logging.getLogger(__name__)

# Папка без записи в базе (загрузка оборвалась, проверку удалили, а файл был занят) — старше часа удаляется.
ORPHAN_AGE_SECONDS = 3600


def delete_expired_sessions(db: Session) -> int:
    result = db.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
    return result.rowcount or 0


def delete_expired_checks(db: Session, settings: Settings) -> int:
    expired = db.scalars(select(Check.id).where(Check.expires_at <= utcnow())).all()
    for check_id in expired:
        storage.remove_check_files(settings, check_id)
    if expired:
        db.execute(delete(Check).where(Check.id.in_(expired)))
    return len(expired)


def delete_read_sources(db: Session, settings: Settings) -> int:
    """Файлы проверок, чтение которых закончилось.

    Обычно файл удаляет само чтение. Здесь — то, что осталось, если чтение оборвалось окончательно
    (кончились повторы после сбоя) или файл не удалось удалить сразу.
    """
    root = storage.checks_root(settings)
    if not root.is_dir():
        return 0
    # Пока проверка ждёт чтения, её файл нужен.
    unread = queue.WAITING_STATUSES[JobKind.EXTRACT]
    finished = set(db.scalars(select(Check.id).where(Check.status.not_in(unread))).all())
    removed = 0
    for path in root.iterdir():
        if path.name in finished:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def delete_orphan_files(db: Session, settings: Settings) -> int:
    removed = 0
    now = time.time()
    root = storage.checks_root(settings)
    if root.is_dir():
        known = set(db.scalars(select(Check.id)).all())
        for path in root.iterdir():
            if path.name in known or now - path.stat().st_mtime < ORPHAN_AGE_SECONDS:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    uploads = storage.uploads_root(settings)
    if uploads.is_dir():
        for path in uploads.iterdir():
            if now - path.stat().st_mtime >= ORPHAN_AGE_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
    # Временные папки заданий Codex: если worker упал посреди задания, в них остался ответ модели.
    temp = storage.temp_root(settings)
    if temp.is_dir():
        for path in temp.iterdir():
            if now - path.stat().st_mtime >= ORPHAN_AGE_SECONDS:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
    return removed


def run_cleanup(db: Session, settings: Settings) -> dict[str, int]:
    result = {
        "sessions": delete_expired_sessions(db),
        "checks": delete_expired_checks(db, settings),
        "sources": delete_read_sources(db, settings),
        "orphans": delete_orphan_files(db, settings),
    }
    if any(result.values()):
        log.info("очистка: %s", result)
    return result
