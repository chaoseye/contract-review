"""Фоновый процесс (контейнер worker): задачи из очереди и периодическая очистка.

WORKER_CONCURRENCY потоков забирают задачи из таблицы jobs. Основной поток раз в минуту
возвращает зависшие задачи в очередь и раз в 10 минут удаляет просроченные сессии и проверки
и оставшиеся после чтения исходные файлы.
"""

import logging
import os
import signal
import socket
import threading
import time
import traceback
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker, session_scope
from app.core.logging import configure_logging
from app.jobs import queue
from app.jobs.cleanup import run_cleanup
from app.jobs.tasks import HANDLERS, RETRY_UNEXPECTED
from app.models import Check

log = logging.getLogger("app.worker")

POLL_SECONDS = 1.0
RECOVER_INTERVAL_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 600


def _frames(exc: BaseException) -> str:
    return "".join(traceback.format_tb(exc.__traceback__)).rstrip()


def process_job(
    factory: sessionmaker[Session], settings: Settings, job_id: int, kind: str, check_id: str
) -> None:
    handler = HANDLERS.get(kind)
    try:
        if handler is None:
            raise RuntimeError(f"неизвестный тип задачи: {kind}")
        handler(factory, settings, job_id, check_id)
    except queue.RetryLater as exc:
        with session_scope(factory) as db:
            requeued = queue.fail(db, job_id, exc.cause, retry=True, final_message=exc.message)
            check = db.get(Check, check_id)
            if requeued and check is not None and queue.waits_for(check, kind):
                check.progress = exc.progress[:200]
        log.info(
            "задача %s (%s): %s, %s",
            job_id,
            kind,
            exc.cause,
            "повтор позже" if requeued else "повторы кончились",
        )
        return
    except Exception as exc:
        # В лог — тип ошибки и место в коде, но не текст исключения: в нём может оказаться фрагмент договора.
        log.error("задача %s (%s) завершилась ошибкой %s\n%s", job_id, kind, type(exc).__name__, _frames(exc))
        with session_scope(factory) as db:
            requeued = queue.fail(db, job_id, type(exc).__name__, retry=RETRY_UNEXPECTED.get(kind, True))
        log.info("задача %s: %s", job_id, "повтор позже" if requeued else "больше не повторяется")
        return
    with session_scope(factory) as db:
        queue.finish(db, job_id)


def _job_loop(
    factory: sessionmaker[Session], settings: Settings, stop: threading.Event, worker_id: str
) -> None:
    while not stop.is_set():
        try:
            with factory() as db:
                job = queue.claim(db, worker_id)
                claimed = (job.id, job.kind, job.check_id) if job is not None else None
        except Exception as exc:
            log.error("не удалось взять задачу из очереди (%s)\n%s", type(exc).__name__, _frames(exc))
            stop.wait(5)
            continue
        if claimed is None:
            stop.wait(POLL_SECONDS)
            continue
        try:
            process_job(factory, settings, *claimed)
        except Exception as exc:
            # Сбой при записи итога задачи (например, база недоступна) не должен останавливать поток: задача
            # останется «в работе» и вернётся в очередь как зависшая.
            log.error("задача %s: итог не записан (%s)\n%s", claimed[0], type(exc).__name__, _frames(exc))
            stop.wait(5)


def _maintenance(factory: sessionmaker[Session], settings: Settings, stop: threading.Event) -> None:
    next_recover = next_cleanup = 0.0
    while not stop.is_set():
        now = time.monotonic()
        try:
            if now >= next_recover:
                with session_scope(factory) as db:
                    recovered = queue.recover_stale(db)
                if recovered:
                    log.warning("возвращено в очередь зависших задач: %s", recovered)
                next_recover = now + RECOVER_INTERVAL_SECONDS
            if now >= next_cleanup:
                with session_scope(factory) as db:
                    run_cleanup(db, settings)
                next_cleanup = now + CLEANUP_INTERVAL_SECONDS
        except Exception as exc:
            log.error("ошибка обслуживания (%s), повторю позже\n%s", type(exc).__name__, _frames(exc))
        stop.wait(5)


def run_worker(settings: Settings, stop: threading.Event) -> None:
    engine = make_engine(settings.database_url)
    factory = make_sessionmaker(engine)
    # SQLite не умеет SKIP LOCKED и плохо переносит параллельную запись: в разработке один поток.
    concurrency = 1 if settings.is_sqlite else settings.worker_concurrency
    base_id = f"{socket.gethostname()}-{os.getpid()}"
    threads = [
        threading.Thread(
            target=_job_loop,
            args=(factory, settings, stop, f"{base_id}-{index}"),
            name=f"job-{index}",
            daemon=True,
        )
        for index in range(concurrency)
    ]
    for thread in threads:
        thread.start()
    log.info("worker запущен, потоков задач: %s", concurrency)
    try:
        _maintenance(factory, settings, stop)
    finally:
        for thread in threads:
            thread.join(timeout=30)
        engine.dispose()
        log.info("worker остановлен")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()

    def _stop(signum: int, _frame: FrameType | None) -> None:
        log.info("получен сигнал %s, останавливаюсь", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_worker(settings, stop)


if __name__ == "__main__":
    main()
