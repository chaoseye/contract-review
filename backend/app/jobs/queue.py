"""Очередь задач в таблице jobs.

Worker забирает задачу через SELECT … FOR UPDATE SKIP LOCKED: два потока или два контейнера не возьмут
одну задачу. На SQLite (разработка) блокировки нет, поэтому там worker работает в один поток.
Пока задача выполняется, worker обновляет locked_at. Задача без обновлений дольше STALE_AFTER считается
зависшей (worker упал) и возвращается в очередь.
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.core.db import session_scope, utcnow
from app.models import Check, CheckStatus, Job, JobKind, JobStatus

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=5)
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
INTERNAL_ERROR_MESSAGE = (
    "Не удалось обработать документ из-за внутренней ошибки. Попробуйте загрузить файл ещё раз."
)
# Документ уже прочитан: загружать его заново не нужно, достаточно запустить анализ.
ANALYSIS_INTERNAL_ERROR_MESSAGE = (
    "Анализ не выполнен из-за внутренней ошибки. Запустите анализ ещё раз, а если ошибка повторится, "
    "сообщите администратору."
)
# Пока проверка в этих статусах, она ждёт результата задачи. Задача у проверки, которая ушла дальше, ничего не
# меняет: ни поздний сбой (сбой чтения не портит идущий анализ), ни повтор после незаписанного итога.
WAITING_STATUSES = {
    JobKind.EXTRACT: (CheckStatus.UPLOADED, CheckStatus.EXTRACTING),
    # extracted — проверки, поставленные в очередь до 2026-10-19, когда анализ ждал в этом статусе.
    JobKind.ANALYZE: (CheckStatus.EXTRACTED, CheckStatus.ANALYZING),
}
# Как часто задача подаёт признаки жизни, пока ждёт внешнюю программу или модель.
HEARTBEAT_SECONDS = 30.0


class RetryLater(Exception):
    """Задачу стоит повторить позже (например, провайдер ИИ временно недоступен).

    message — для менеджера, если повторы кончились; progress — что показать, пока задача ждёт повтора.
    Тексты пишет код системы, фрагментов договора в них нет.
    """

    def __init__(self, message: str, progress: str, cause: str) -> None:
        super().__init__(message)
        self.message = message
        self.progress = progress
        self.cause = cause


def enqueue(db: Session, kind: str, check_id: str, max_attempts: int = 2) -> Job:
    job = Job(
        kind=kind, check_id=check_id, status=JobStatus.QUEUED, max_attempts=max_attempts, run_after=utcnow()
    )
    db.add(job)
    return job


def claim(db: Session, worker_id: str) -> Job | None:
    """Забрать следующую задачу и отметить её выполняемой. Делает commit."""
    now = utcnow()
    job = db.scalar(
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.rollback()
        return None
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.locked_at = now
    job.locked_by = worker_id
    job.error = None
    db.commit()
    return job


def heartbeat(db: Session, job_id: int) -> None:
    db.execute(
        update(Job).where(Job.id == job_id, Job.status == JobStatus.RUNNING).values(locked_at=utcnow())
    )


def finish(db: Session, job_id: int) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.DONE, locked_at=None, locked_by=None, error=None, updated_at=utcnow())
    )


def internal_error_message(kind: str) -> str:
    return ANALYSIS_INTERNAL_ERROR_MESSAGE if kind == JobKind.ANALYZE else INTERNAL_ERROR_MESSAGE


def fail(db: Session, job_id: int, error: str, retry: bool, final_message: str | None = None) -> bool:
    """Отметить ошибку. Возвращает True, если задача вернулась в очередь на повтор.

    Когда повторы кончились, проверка получает статус failed и сообщение final_message (по умолчанию —
    о внутренней ошибке, своё для чтения и для анализа).
    """
    job = db.get(Job, job_id)
    if job is None:
        return False
    job.locked_at = None
    job.locked_by = None
    job.error = error[:2000]
    if retry and job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.run_after = utcnow() + RETRY_DELAYS[min(job.attempts, len(RETRY_DELAYS)) - 1]
        return True
    job.status = JobStatus.FAILED
    _fail_check(db, job, final_message or internal_error_message(job.kind))
    return False


def has_pending(db: Session, check_id: str, kind: str) -> bool:
    """Есть ли у проверки задача этого вида в очереди или в работе."""
    return (
        db.scalar(
            select(Job.id)
            .where(
                Job.check_id == check_id,
                Job.kind == kind,
                Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
            )
            .limit(1)
        )
        is not None
    )


@contextmanager
def keep_alive(
    factory: sessionmaker[Session], job_id: int, interval: float = HEARTBEAT_SECONDS
) -> Iterator[None]:
    """Обновлять locked_at из отдельного потока, пока выполняется тело блока.

    Ответ модели может идти дольше STALE_AFTER: без признаков жизни задачу сочли бы зависшей.
    """
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(interval):
            try:
                with session_scope(factory) as db:
                    heartbeat(db, job_id)
            except Exception as exc:
                log.warning("задача %s: признак жизни не записан (%s)", job_id, type(exc).__name__)

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def recover_stale(db: Session) -> int:
    """Вернуть в очередь задачи, которые выполнялись, но давно не подавали признаков жизни."""
    threshold = utcnow() - STALE_AFTER
    stale = db.scalars(
        select(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < threshold)
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        fail(db, job.id, "Задача зависла: worker перестал отвечать", retry=True)
    return len(stale)


def waits_for(check: Check, kind: str) -> bool:
    """Ждёт ли проверка результата задачи этого вида."""
    waiting = WAITING_STATUSES.get(kind)
    if waiting is None:
        return check.status not in (CheckStatus.READY, CheckStatus.EXTRACTED)
    return check.status in waiting


def _fail_check(db: Session, job: Job, message: str) -> None:
    check = db.get(Check, job.check_id)
    if check is None or not waits_for(check, job.kind):
        return
    check.status = CheckStatus.FAILED
    check.progress = None
    check.error = message
    if job.kind == JobKind.ANALYZE:
        # Неудачный анализ — в журнале, и когда кончились повторы, и при внутренней ошибке.
        model = (check.analysis or {}).get("model")
        audit.record(db, "analysis_failed", user_id=check.owner_id, check=check, model=model, details=message)
