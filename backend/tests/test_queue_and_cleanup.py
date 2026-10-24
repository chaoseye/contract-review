"""Очередь задач и очистка по сроку.

Тест параллельного захвата задач работает только на PostgreSQL: задайте TEST_POSTGRES_URL с отдельной
базой, имя которой оканчивается на _test (например, postgresql+pg8000://contract_review:…@db:5432/contract_review_test).
Тест пересоздаёт её схему. Без переменной тест пропускается.
"""

import os
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, text

from app import storage
from app.cli import alembic_config
from app.core.db import make_engine, make_sessionmaker, utcnow
from app.jobs import queue, worker
from app.jobs.cleanup import ORPHAN_AGE_SECONDS, run_cleanup
from app.models import AuditEntry, Check, CheckStatus, Job, JobStatus, UserSession
from tests.conftest import add_user


def make_check(factory, owner_id: int, expires_in: timedelta = timedelta(hours=24)) -> str:
    with factory() as db:
        check = Check(
            owner_id=owner_id,
            status=CheckStatus.UPLOADED,
            file_name="a.docx",
            file_kind="docx",
            file_size=1,
            comment="",
            expires_at=utcnow() + expires_in,
        )
        db.add(check)
        db.commit()
        return check.id


def test_claim_takes_jobs_in_order_and_once(db_factory) -> None:
    user_id = add_user(db_factory)
    first, second = make_check(db_factory, user_id), make_check(db_factory, user_id)
    with db_factory() as db:
        queue.enqueue(db, "extract", first)
        queue.enqueue(db, "extract", second)
        db.commit()
    with db_factory() as db:
        job_a = queue.claim(db, "w1")
        a = (job_a.check_id, job_a.status, job_a.attempts, job_a.locked_by)
        job_b = queue.claim(db, "w2")
        b = job_b.check_id
        assert queue.claim(db, "w3") is None
    assert a == (first, JobStatus.RUNNING, 1, "w1")
    assert b == second


def test_delayed_job_waits(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        job = queue.enqueue(db, "extract", check_id)
        job.run_after = utcnow() + timedelta(minutes=5)
        db.commit()
    with db_factory() as db:
        assert queue.claim(db, "w") is None


def test_fail_retries_then_marks_check_failed(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        queue.enqueue(db, "extract", check_id, max_attempts=2)
        db.commit()
    for expected_requeue in (True, False):
        with db_factory() as db:
            job = queue.claim(db, "w")
            assert job is not None
            assert queue.fail(db, job.id, "Ошибка", retry=True) is expected_requeue
            job.run_after = utcnow()
            db.commit()
    with db_factory() as db:
        assert db.scalars(select(Job.status)).one() == JobStatus.FAILED
        check = db.get(Check, check_id)
        assert check.status == CheckStatus.FAILED
        assert check.error == queue.INTERNAL_ERROR_MESSAGE


def test_stale_running_job_is_requeued(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        queue.enqueue(db, "extract", check_id)
        db.commit()
    with db_factory() as db:
        job = queue.claim(db, "w")
        job.locked_at = utcnow() - queue.STALE_AFTER - timedelta(seconds=1)
        db.commit()
    with db_factory() as db:
        assert queue.recover_stale(db) == 1
        db.commit()
    with db_factory() as db:
        job = db.scalars(select(Job)).one()
        assert job.status == JobStatus.QUEUED
        assert job.locked_by is None


def test_late_extract_failure_does_not_touch_check_that_moved_on(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        queue.enqueue(db, "extract", check_id, max_attempts=1)
        db.commit()
    with db_factory() as db:
        job = queue.claim(db, "w")
        # Чтение успело закончиться, и анализ уже поставлен в очередь.
        db.get(Check, check_id).status = CheckStatus.ANALYZING
        assert queue.fail(db, job.id, "Ошибка", retry=True) is False
        db.commit()
    with db_factory() as db:
        check = db.get(Check, check_id)
        assert (check.status, check.error) == (CheckStatus.ANALYZING, None)


def test_final_analyze_failure_marks_check_and_audit(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        db.get(Check, check_id).status = CheckStatus.ANALYZING
        queue.enqueue(db, "analyze", check_id, max_attempts=1)
        db.commit()
    with db_factory() as db:
        job = queue.claim(db, "w")
        job.locked_at = utcnow() - queue.STALE_AFTER - timedelta(seconds=1)
        db.commit()
    with db_factory() as db:
        # Worker упал во время анализа, повторов не осталось.
        assert queue.recover_stale(db) == 1
        db.commit()
    with db_factory() as db:
        check = db.get(Check, check_id)
        assert (check.status, check.progress) == (CheckStatus.FAILED, None)
        assert check.error == queue.ANALYSIS_INTERNAL_ERROR_MESSAGE
        actions = db.scalars(select(AuditEntry.action).where(AuditEntry.check_id == check_id)).all()
        assert actions == ["analysis_failed"]


def test_job_loop_survives_error_while_recording_result(db_factory, settings, monkeypatch) -> None:
    user_id = add_user(db_factory)
    check_ids = [make_check(db_factory, user_id) for _ in range(2)]
    with db_factory() as db:
        for check_id in check_ids:
            queue.enqueue(db, "extract", check_id)
        db.commit()
    stop = threading.Event()
    calls: list[int] = []

    def broken(factory, settings, job_id, kind, check_id) -> None:
        calls.append(job_id)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(worker, "process_job", broken)
    monkeypatch.setattr(stop, "wait", lambda timeout=None: stop.is_set())
    worker._job_loop(db_factory, settings, stop, "w")
    # Раньше исключение из process_job завершало поток задач, и следующую задачу никто не брал.
    assert len(calls) == 2


def test_heartbeat_keeps_job_alive(db_factory) -> None:
    user_id = add_user(db_factory)
    check_id = make_check(db_factory, user_id)
    with db_factory() as db:
        queue.enqueue(db, "extract", check_id)
        db.commit()
    with db_factory() as db:
        job = queue.claim(db, "w")
        job.locked_at = utcnow() - queue.STALE_AFTER - timedelta(seconds=1)
        db.commit()
        queue.heartbeat(db, job.id)
        db.commit()
    with db_factory() as db:
        assert queue.recover_stale(db) == 0


def test_cleanup_removes_expired_checks_files_sessions_and_orphans(db_factory, settings) -> None:
    user_id = add_user(db_factory)
    expired = make_check(db_factory, user_id, expires_in=timedelta(seconds=-1))
    alive = make_check(db_factory, user_id)
    for check_id in (expired, alive):
        storage.source_path(settings, check_id, "docx").parent.mkdir(parents=True)
        storage.source_path(settings, check_id, "docx").write_bytes(b"x")
    with db_factory() as db:
        queue.enqueue(db, "extract", expired)
        db.add(UserSession(user_id=user_id, token_hash="e" * 64, expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()

    orphan = storage.checks_root(settings) / ("f" * 32)
    orphan.mkdir(parents=True)
    old_upload = storage.uploads_root(settings) / "old.part"
    old_upload.parent.mkdir(parents=True)
    old_upload.write_bytes(b"x")
    fresh_orphan = storage.checks_root(settings) / ("e" * 32)
    fresh_orphan.mkdir()
    past = time.time() - ORPHAN_AGE_SECONDS - 10
    os.utime(orphan, (past, past))
    os.utime(old_upload, (past, past))

    with db_factory() as db:
        result = run_cleanup(db, settings)
        db.commit()
    assert result == {"sessions": 1, "checks": 1, "sources": 0, "orphans": 2}
    assert not storage.check_dir(settings, expired).exists()
    assert storage.check_dir(settings, alive).exists()
    assert not orphan.exists() and not old_upload.exists()
    assert fresh_orphan.exists()
    with db_factory() as db:
        assert db.scalars(select(Check.id)).all() == [alive]
        assert db.scalars(select(Job)).all() == []


def test_cleanup_removes_sources_that_will_not_be_read(db_factory, settings) -> None:
    """Файл остаётся, только пока проверка ждёт чтения. Остальное — остаток после сбоя, удаляется."""
    user_id = add_user(db_factory)
    checks = {status: make_check(db_factory, user_id) for status in CheckStatus}
    with db_factory() as db:
        for status, check_id in checks.items():
            db.get(Check, check_id).status = status
            source = storage.source_path(settings, check_id, "docx")
            source.parent.mkdir(parents=True)
            source.write_bytes(b"x")
        db.commit()
        result = run_cleanup(db, settings)
        db.commit()
    assert result["sources"] == 4
    kept = {status for status, check_id in checks.items() if storage.check_dir(settings, check_id).exists()}
    assert kept == {CheckStatus.UPLOADED, CheckStatus.EXTRACTING}
    with db_factory() as db:
        assert len(db.scalars(select(Check.id)).all()) == len(CheckStatus)  # сами проверки остаются


POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="нужен TEST_POSTGRES_URL (запускается в Docker)")
def test_parallel_workers_never_take_same_job_on_postgres(tmp_path: Path) -> None:
    from sqlalchemy.engine import make_url

    # Тест пересоздаёт схему базы: защита от запуска на рабочей базе.
    database = make_url(POSTGRES_URL).database or ""
    assert database.endswith("_test"), "TEST_POSTGRES_URL должен указывать на отдельную базу *_test"
    engine = make_engine(POSTGRES_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = alembic_config(POSTGRES_URL)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    factory = make_sessionmaker(engine)
    try:
        user_id = add_user(factory)
        with factory() as db:
            for _ in range(40):
                check = Check(
                    owner_id=user_id, status="uploaded", file_name="a", file_kind="docx", file_size=1,
                    comment="", expires_at=utcnow() + timedelta(hours=1),
                )  # fmt: skip
                db.add(check)
                db.flush()
                queue.enqueue(db, "extract", check.id)
            db.commit()
        taken: list[int] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            while True:
                with factory() as db:
                    job = queue.claim(db, name)
                if job is None:
                    return
                with lock:
                    taken.append(job.id)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(taken) == 40
        assert len(set(taken)) == 40
    finally:
        engine.dispose()
