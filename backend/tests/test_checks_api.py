"""API проверок: загрузка, права, срок жизни, удаление, обработка задачи."""

import logging
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import storage
from app.api.middleware import CSRF_HEADER, CSRF_HEADER_VALUE
from app.core.db import utcnow
from app.jobs import queue
from app.jobs.cleanup import run_cleanup
from app.jobs.worker import process_job
from app.main import create_app
from app.models import Check, CheckStatus, Job, UserRole
from tests.conftest import PASSWORD, add_user, make_settings, migrate
from tests.docx_builder import make_docx, p

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def login(client: TestClient, name: str) -> None:
    response = client.post("/api/auth/login", json={"login": name, "password": PASSWORD})
    assert response.status_code == 200


def docx_bytes(tmp_path: Path, *texts: str) -> bytes:
    path = make_docx(tmp_path / "upload.docx", "".join(p(text) for text in texts))
    return path.read_bytes()


def upload(client: TestClient, data: bytes, name: str = "Договор.docx", comment: str = ""):
    return client.post("/api/checks", files={"file": (name, data, DOCX_MIME)}, data={"comment": comment})


def run_next_job(db_factory, settings) -> None:
    with db_factory() as db:
        job = queue.claim(db, "test-worker")
        assert job is not None
        claimed = (job.id, job.kind, job.check_id)
    process_job(db_factory, settings, *claimed)


def test_upload_creates_check_file_and_job(client: TestClient, db_factory, settings, tmp_path: Path) -> None:
    add_user(db_factory)
    login(client, "manager")
    response = upload(
        client, docx_bytes(tmp_path, "1.1. Текст"), name="C:\\Users\\x\\Договор №5.docx", comment=" срочно "
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["file_name"] == "Договор №5.docx"
    assert body["file_kind"] == "docx"
    assert body["owner_login"] is None
    assert storage.source_path(settings, body["id"], "docx").is_file()
    with db_factory() as db:
        check = db.get(Check, body["id"])
        assert check.comment == "срочно"
        assert check.expires_at > utcnow() + timedelta(hours=23)
        jobs = db.scalars(select(Job)).all()
        assert [(job.kind, job.status, job.check_id) for job in jobs] == [("extract", "queued", body["id"])]


def test_extract_job_fills_blocks(client: TestClient, db_factory, settings, tmp_path: Path) -> None:
    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "1. ПРЕДМЕТ", "1.1. Поставщик передаёт товар.")).json()[
        "id"
    ]
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "extracted"
    assert detail["progress"] is None
    assert [(b["id"], b["clause"]) for b in detail["blocks"]] == [("B0001", "1"), ("B0002", "1.1")]
    # Текст в базе, исходный файл и промежуточные файлы удалены сразу после чтения.
    assert not storage.check_dir(settings, check_id).exists()
    with db_factory() as db:
        assert db.scalars(select(Job.status)).all() == ["done"]


def test_unreadable_document_fails_check_with_message(
    client: TestClient, db_factory, settings, tmp_path
) -> None:
    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "")).json()["id"]
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"] == "В документе не найден текст."
    assert not storage.check_dir(settings, check_id).exists()
    with db_factory() as db:
        assert db.scalars(select(Job.status)).all() == ["done"]


def test_unexpected_error_is_retried_then_reported(
    client, db_factory, settings, tmp_path, monkeypatch
) -> None:
    from app.jobs import tasks

    def explode(*_args, **_kwargs):
        raise RuntimeError("секретный текст договора")

    monkeypatch.setattr(tasks, "extract_document", explode)
    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "1.1. Текст")).json()["id"]
    run_next_job(db_factory, settings)
    source = storage.source_path(settings, check_id, "docx")
    assert source.is_file()  # повтор прочитает файл снова
    with db_factory() as db:
        job = db.scalars(select(Job)).one()
        assert (job.status, job.attempts, job.error) == ("queued", 1, "RuntimeError")
        job.run_after = utcnow()
        db.commit()
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert "внутренней ошибки" in detail["error"]
    assert "секретный" not in detail["error"]
    # Повторов больше не будет: файл, оставшийся после сбоя, удаляет ближайшая очистка.
    with db_factory() as db:
        assert run_cleanup(db, settings)["sources"] == 1
        db.commit()
    assert not storage.check_dir(settings, check_id).exists()
    assert client.get(f"/api/checks/{check_id}").json()["status"] == "failed"


def test_source_is_kept_until_reading_result_is_saved(
    client, db_factory, settings, tmp_path, monkeypatch
) -> None:
    """Итог чтения не записался (например, база недоступна): файл остаётся, и повтор читает его снова."""
    from app.jobs import tasks

    real_start = tasks.start_analysis

    def database_down(*_args, **_kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(tasks, "start_analysis", database_down)
    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "1.1. Текст")).json()["id"]
    run_next_job(db_factory, settings)
    assert storage.source_path(settings, check_id, "docx").is_file()
    assert client.get(f"/api/checks/{check_id}").json()["blocks"] is None

    monkeypatch.setattr(tasks, "start_analysis", real_start)
    with db_factory() as db:
        db.scalars(select(Job)).one().run_after = utcnow()
        db.commit()
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "extracted"
    assert [b["clause"] for b in detail["blocks"]] == ["1.1"]
    assert not storage.check_dir(settings, check_id).exists()


def test_second_execution_after_finished_reading_exits_quietly(
    client, db_factory, settings, tmp_path, monkeypatch, caplog
) -> None:
    """Задачу сочли зависшей, пока она шла: другое выполнение записало итог и удалило файл, и это
    чтение упало на пропавшем файле. Оно завершается тихо: проверка не меняется, ошибки в журнале нет."""
    from app.jobs import tasks

    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "1.1. Текст")).json()["id"]
    finished = [{"id": "B0001", "clause": "1.1", "text": "Текст"}]

    def other_execution_finishes_meanwhile(source, work, progress=None):
        with db_factory() as db:
            check = db.get(Check, check_id)
            check.status, check.blocks = CheckStatus.EXTRACTED, finished
            db.commit()
        storage.remove_check_files(settings, check_id)
        raise FileNotFoundError(source)

    monkeypatch.setattr(tasks, "extract_document", other_execution_finishes_meanwhile)
    with caplog.at_level(logging.INFO):
        run_next_job(db_factory, settings)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    with db_factory() as db:
        check = db.get(Check, check_id)
        assert (check.status, check.blocks, check.error) == (CheckStatus.EXTRACTED, finished, None)
        assert db.scalars(select(Job.status)).all() == ["done"]


def test_unsupported_file_is_rejected_without_leftovers(client: TestClient, db_factory, settings) -> None:
    add_user(db_factory)
    login(client, "manager")
    response = upload(client, "Просто текст".encode(), name="a.txt")
    assert response.status_code == 415
    assert "docx, doc или PDF" in response.json()["detail"]
    with db_factory() as db:
        assert db.scalars(select(Check)).all() == []
    uploads = storage.uploads_root(settings)
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_empty_file_is_rejected(client: TestClient, db_factory) -> None:
    add_user(db_factory)
    login(client, "manager")
    assert upload(client, b"").status_code == 400


def test_too_large_file_is_rejected(tmp_path: Path, db_factory) -> None:
    settings = make_settings(tmp_path, max_upload_mb=1)
    migrate(settings)
    add_user(db_factory)
    with TestClient(create_app(settings)) as client:
        client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        login(client, "manager")
        # Чуть больше лимита: Content-Length ещё пропускает middleware, отказывает обработчик.
        assert upload(client, b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)).status_code == 413
        # Намного больше: отказ по Content-Length до разбора запроса.
        assert upload(client, b"%PDF-1.7\n" + b"0" * (3 * 1024 * 1024)).status_code == 413
        uploads = storage.uploads_root(settings)
        assert not uploads.exists() or list(uploads.iterdir()) == []


def test_upload_requires_login_and_csrf_header(client: TestClient, db_factory, tmp_path) -> None:
    assert upload(client, docx_bytes(tmp_path, "текст")).status_code == 401
    add_user(db_factory)
    login(client, "manager")
    del client.headers[CSRF_HEADER]
    assert upload(client, docx_bytes(tmp_path, "текст")).status_code == 403


def test_managers_see_only_their_checks_and_admin_sees_all(app, db_factory, tmp_path) -> None:
    add_user(db_factory, login="ivanov")
    add_user(db_factory, login="petrov")
    add_user(db_factory, login="boss", role=UserRole.ADMIN)
    clients = {}
    for name in ("ivanov", "petrov", "boss"):
        client = TestClient(app)
        client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        login(client, name)
        clients[name] = client
    ivanov_check = upload(clients["ivanov"], docx_bytes(tmp_path, "текст")).json()["id"]
    upload(clients["petrov"], docx_bytes(tmp_path, "текст"))

    assert [c["id"] for c in clients["ivanov"].get("/api/checks").json()] == [ivanov_check]
    assert clients["petrov"].get(f"/api/checks/{ivanov_check}").status_code == 404
    assert clients["petrov"].delete(f"/api/checks/{ivanov_check}").status_code == 404

    admin_list = clients["boss"].get("/api/checks").json()
    assert sorted(c["owner_login"] for c in admin_list) == ["ivanov", "petrov"]
    assert clients["boss"].get(f"/api/checks/{ivanov_check}").status_code == 200
    for client in clients.values():
        client.close()


def test_owner_activity_extends_lifetime_but_admin_view_does_not(app, db_factory, tmp_path) -> None:
    add_user(db_factory, login="ivanov")
    add_user(db_factory, login="boss", role=UserRole.ADMIN)
    owner, admin = TestClient(app), TestClient(app)
    for client, name in ((owner, "ivanov"), (admin, "boss")):
        client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        login(client, name)
    check_id = upload(owner, docx_bytes(tmp_path, "текст")).json()["id"]
    old = utcnow() - timedelta(hours=2)
    with db_factory() as db:
        check = db.get(Check, check_id)
        check.last_activity_at = old
        check.expires_at = old + timedelta(hours=24)
        db.commit()
    admin.get(f"/api/checks/{check_id}")
    with db_factory() as db:
        assert db.get(Check, check_id).last_activity_at == old
    owner.get(f"/api/checks/{check_id}")
    with db_factory() as db:
        assert db.get(Check, check_id).expires_at > utcnow() + timedelta(hours=23)
    owner.close()
    admin.close()


def test_delete_removes_row_files_and_jobs(client: TestClient, db_factory, settings, tmp_path) -> None:
    add_user(db_factory)
    login(client, "manager")
    check_id = upload(client, docx_bytes(tmp_path, "текст")).json()["id"]
    assert client.delete(f"/api/checks/{check_id}").status_code == 204
    assert client.get(f"/api/checks/{check_id}").status_code == 404
    assert not storage.check_dir(settings, check_id).exists()
    with db_factory() as db:
        assert db.scalars(select(Job)).all() == []
