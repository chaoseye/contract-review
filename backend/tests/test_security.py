"""Этап 6: проверки безопасности — права на проверки, логи без текстов, загрузка подменённых
и повреждённых файлов, заголовки, предел тела запросов, удаление проверки по сроку.
"""

import io
import logging
import re
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import storage
from app.analysis import profiles, run
from app.api import middleware
from app.api.middleware import CSRF_HEADER, CSRF_HEADER_VALUE
from app.core.db import utcnow
from app.documents import docx_reader
from app.documents.errors import DamagedFileError
from app.jobs.cleanup import run_cleanup
from app.models import AuditEntry, Check, Job, UserRole
from tests.analysis_helpers import FakeProvider, answer, risk
from tests.conftest import add_user, make_settings, migrate
from tests.docx_builder import make_docx, p
from tests.test_analysis_job import CONTRACT, add_profile, uploaded
from tests.test_checks_api import docx_bytes, login, run_next_job, upload
from tests.test_protocol_api import add_organization

COMMENT = "Предоплата 37 процентов, поставка в Тестоград до пятницы"
BUYER = "Акционерное общество «Ромашка-Секрет»"
RISK_TITLE = "Неустойка без верхнего предела для поставщика"
EXPLANATION = "Месяц просрочки обойдётся поставщику в пятнадцать процентов стоимости партии."
WORDING = "9.2. Неустойка поставщика составляет 0,1 % в день, но не более 10 % цены партии."
QUOTE = "Поставщик уплачивает неустойку в размере 0,5% от стоимости товара за каждый день"


def secret_answer() -> dict:
    data = answer(
        risks=[
            risk(
                "R1",
                block_ids=["B0002"],
                quote=QUOTE,
                title=RISK_TITLE,
                explanation=EXPLANATION,
                proposed_wording=WORDING,
            )
        ],
        obligations=[],
        favorable=[],
    )
    data["contract"]["buyer"]["name"] = BUYER
    data["summary"] = "Сводка анализа: условия поставки в Тестоград требуют правок."
    return data


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(profiles, "build_provider", lambda profile, settings, **kwargs: fake)
    return fake


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(parts)


# --- логи -----------------------------------------------------------------------------------------


def test_logs_have_no_contract_texts(
    client: TestClient, db_factory, settings, tmp_path: Path, provider: FakeProvider, caplog
) -> None:
    """Весь путь: загрузка, чтение, анализ, протокол, удаление. В логах — только числа и идентификаторы."""
    caplog.set_level(logging.DEBUG)
    add_user(db_factory)
    add_profile(db_factory)
    add_organization(db_factory)
    login(client, "manager")
    provider.responses.append(secret_answer())
    check_id = uploaded(client, db_factory, settings, tmp_path, comment=COMMENT)
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "ready"
    state = client.get(f"/api/checks/{check_id}/protocol").json()["state"]
    assert client.put(f"/api/checks/{check_id}/protocol", json=state).status_code == 200
    assert client.get(f"/api/checks/{check_id}/protocol.docx").status_code == 200
    assert client.delete(f"/api/checks/{check_id}").status_code == 204

    text = log_text(caplog)
    assert check_id in text  # логи есть, и в них идентификатор проверки
    fragments = [COMMENT, BUYER, RISK_TITLE, EXPLANATION, WORDING, QUOTE, "Тестоград", "Ромашка"]
    fragments += [line[:40] for line in CONTRACT]
    for fragment in fragments:
        assert fragment not in text, fragment


def test_errors_are_logged_without_their_text(
    client: TestClient, db_factory, settings, tmp_path: Path, provider: FakeProvider, caplog, monkeypatch
) -> None:
    """Ответ провайдера и текст исключения могут содержать договор: в лог попадает только тип."""
    caplog.set_level(logging.DEBUG)
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")

    from app.analysis.providers.base import ProviderResponseError

    secret = f"модель процитировала: {CONTRACT[1]}"
    provider.responses.append(ProviderResponseError("Ответ модели не разобран.", detail=secret))
    provider.responses.append(ProviderResponseError("Ответ модели не разобран.", detail=secret))
    first = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    assert client.get(f"/api/checks/{first}").json()["status"] == "failed"

    # Метка создаётся при запуске: в исходном коде (а он виден в стеке вызовов) её нет.
    marker = uuid.uuid4().hex

    def broken(*args, **kwargs):
        raise RuntimeError(f"{marker} {CONTRACT[2]}")

    monkeypatch.setattr(run, "run_analysis", broken)
    monkeypatch.setattr("app.jobs.tasks.run_analysis", broken)
    assert client.post(f"/api/checks/{first}/analyze").status_code == 202
    run_next_job(db_factory, settings)

    text = log_text(caplog)
    assert "ProviderResponseError" in text and "RuntimeError" in text
    for fragment in (CONTRACT[1][:40], CONTRACT[2][:40], marker, "модель процитировала"):
        assert fragment not in text, fragment


# --- права ----------------------------------------------------------------------------------------


def check_routes(app: FastAPI, check_id: str) -> list[tuple[str, str]]:
    routes = []
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/checks/{check_id}"):
            routes += [
                (method.upper(), path.replace("{check_id}", check_id)) for method in sorted(operations)
            ]
    return routes


def test_check_access_matrix(
    app: FastAPI, db_factory, settings, tmp_path: Path, provider: FakeProvider
) -> None:
    for name, role in (("manager", UserRole.MANAGER), ("other", UserRole.MANAGER), ("admin", UserRole.ADMIN)):
        add_user(db_factory, login=name, role=role)
    add_profile(db_factory)
    add_organization(db_factory)
    provider.responses.append(secret_answer())
    clients = {name: TestClient(app) for name in ("manager", "other", "admin", "anonymous")}
    try:
        for name, client in clients.items():
            client.__enter__()
            client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
            if name != "anonymous":
                login(client, name)
        check_id = uploaded(clients["manager"], db_factory, settings, tmp_path)
        run_next_job(db_factory, settings)
        state = clients["manager"].get(f"/api/checks/{check_id}/protocol").json()["state"]
        routes = check_routes(app, check_id)
        # Все операции с проверкой: состояние, анализ, протокол, скачивание, удаление.
        assert len(routes) >= 8

        def call(name: str, method: str, path: str) -> int:
            body = state if method == "PUT" else None
            return clients[name].request(method, path, json=body).status_code

        for method, path in routes:
            assert call("anonymous", method, path) == 401, (method, path)
            # Чужая проверка для менеджера не существует: 404, а не 403.
            assert call("other", method, path) == 404, (method, path)
        # Администратор видит, но не правит и не выпускает чужой протокол.
        admin_codes = {
            (method, path.removeprefix(f"/api/checks/{check_id}")): call("admin", method, path)
            for method, path in routes
            if method != "DELETE"
        }
        assert admin_codes[("GET", "")] == 200 and admin_codes[("GET", "/protocol")] == 200
        for key in (
            ("PUT", "/protocol"),
            ("POST", "/protocol/reset"),
            ("POST", "/analyze"),
            ("GET", "/protocol.docx"),
            ("GET", "/protocol.pdf"),
        ):
            assert admin_codes[key] == 403, key
        # Несуществующая проверка выглядит так же, как чужая.
        assert clients["manager"].get(f"/api/checks/{'0' * 32}").status_code == 404
        assert clients["admin"].delete(f"/api/checks/{check_id}").status_code == 204
    finally:
        for client in clients.values():
            client.__exit__(None, None, None)


# --- загрузка -------------------------------------------------------------------------------------


def zip_bytes(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("Договор.docx", b"MZ\x90\x00\x03" + b"\x00" * 200),  # программа под видом договора
        ("Договор.pdf", b"<!doctype html><script>alert(1)</script>"),
        ("Договор.docx", zip_bytes({"readme.txt": b"not a word document"})),
        ("Договор.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600),  # OLE без документа Word
        ("Договор.docx", zip_bytes({f"part{index}.xml": b"<a/>" for index in range(5001)})),  # zip-бомба
        ("Договор.docx", b"PK\x03\x04" + b"\x00" * 100),  # битый архив
    ],
    ids=["exe", "html", "zip", "ole", "zip-bomb", "broken-zip"],
)
def test_spoofed_and_broken_files_are_rejected(
    client: TestClient, db_factory, settings, name, content
) -> None:
    add_user(db_factory)
    login(client, "manager")
    response = upload(client, content, name=name)
    assert response.status_code == 415, response.text
    with db_factory() as db:
        assert db.scalars(select(Check)).all() == []
    uploads = storage.uploads_root(settings)
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_type_comes_from_content_not_extension(client: TestClient, db_factory, tmp_path: Path) -> None:
    add_user(db_factory)
    login(client, "manager")
    docx = docx_bytes(tmp_path, "1.1. Текст")
    pdf = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF"
    assert upload(client, docx, name="Договор.pdf").json()["file_kind"] == "docx"
    assert upload(client, pdf, name="Договор.docx").json()["file_kind"] == "pdf"


def corrupted_docx(tmp_path: Path) -> bytes:
    """Настоящий docx, у которого после упаковки испорчен основной текст: CRC части не сходится."""
    source = make_docx(tmp_path / "ok.docx", p("1.1. Текст пункта договора"))
    with zipfile.ZipFile(source) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    raw = zip_bytes(entries, zipfile.ZIP_STORED)
    assert raw.count(b"w:body") >= 1
    return raw.replace(b"w:body", b"w:bodz", 1)


def test_damaged_documents_fail_with_message(
    client: TestClient, db_factory, settings, tmp_path: Path
) -> None:
    add_user(db_factory)
    login(client, "manager")
    cases = {
        "crc.docx": corrupted_docx(tmp_path),
        "xml.docx": make_docx(tmp_path / "xml.docx", "<w:p><w:r><w:t>незакрытый").read_bytes(),
        "short.pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Type /Catalog",
    }
    for name, content in cases.items():
        response = upload(client, content, name=name)
        assert response.status_code == 201, (name, response.text)
        run_next_job(db_factory, settings)
        detail = client.get(f"/api/checks/{response.json()['id']}").json()
        # Понятное сообщение сразу, без повторов и без «внутренней ошибки».
        assert detail["status"] == "failed", name
        assert detail["error"].startswith("Файл повреждён"), (name, detail["error"])
    with db_factory() as db:
        assert {job.status for job in db.scalars(select(Job))} == {"done"}


def test_oversized_xml_part_is_rejected(tmp_path: Path, monkeypatch) -> None:
    path = make_docx(tmp_path / "big.docx", p("1.1. " + "длинный текст " * 200))
    monkeypatch.setattr(docx_reader, "MAX_XML_PART_BYTES", 1000)
    with pytest.raises(DamagedFileError, match="слишком велик"):
        docx_reader.read_docx(path)


# --- заголовки и размер запросов ------------------------------------------------------------------


def test_security_headers(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>contract-review</title>", encoding="utf-8")
    (static / "assets" / "app-1.js").write_text("console.log(1)", encoding="utf-8")
    settings = make_settings(tmp_path, static_dir=static)
    migrate(settings)
    with TestClient(create_app_for(settings)) as client:
        for path in ("/", "/admin/users", "/assets/app-1.js"):
            headers = client.get(path).headers
            policy = headers["content-security-policy"]
            assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy, path
            assert "unsafe-eval" not in policy and "http" not in policy, path
            assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
            assert "camera=()" in headers["permissions-policy"]
        api = client.get("/api/health").headers
        assert "content-security-policy" not in api and api["cache-control"] == "no-store"
        assert "camera=()" in api["permissions-policy"]


def create_app_for(settings):  # type: ignore[no-untyped-def]
    from app.main import create_app

    return create_app(settings)


def test_large_api_bodies_are_rejected_before_parsing(client: TestClient, db_factory, monkeypatch) -> None:
    add_user(db_factory, login="admin", role=UserRole.ADMIN)
    login(client, "admin")
    monkeypatch.setattr(middleware, "MAX_API_BODY_BYTES", 1000)
    big = {"text": "x" * 2000, "comment": ""}
    response = client.post("/api/admin/prompts", json=big)
    assert response.status_code == 413 and response.json()["detail"] == "Слишком большой запрос."
    assert client.post("/api/admin/prompts", json={"text": "короткий", "comment": ""}).status_code == 201


# --- удаление по сроку ----------------------------------------------------------------------------


def test_expired_check_is_removed_with_everything_but_the_journal(
    client: TestClient, db_factory, settings, tmp_path: Path, provider: FakeProvider
) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    add_organization(db_factory)
    login(client, "manager")
    provider.responses.append(secret_answer())
    check_id = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    state = client.get(f"/api/checks/{check_id}/protocol").json()["state"]
    client.put(f"/api/checks/{check_id}/protocol", json=state)
    client.get(f"/api/checks/{check_id}/protocol.docx")
    # Файла договора на диске уже нет: он удалён сразу после чтения. Остались текст и результат в базе.
    assert not storage.check_dir(settings, check_id).exists()

    with db_factory() as db:
        db.execute(update(Check).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
        result = run_cleanup(db, settings)
        db.commit()
    assert result["checks"] == 1
    assert not storage.check_dir(settings, check_id).exists()
    assert client.get(f"/api/checks/{check_id}").status_code == 404
    with db_factory() as db:
        assert db.scalars(select(Check)).all() == [] and db.scalars(select(Job)).all() == []
        # Журнал остаётся: в нём нет текстов, только факты.
        actions = [
            row.action for row in db.scalars(select(AuditEntry).where(AuditEntry.check_id == check_id))
        ]
        assert actions == ["upload", "analysis_done", "protocol_docx"]
        entries = db.scalars(select(AuditEntry)).all()
        assert all(QUOTE not in str(vars(entry)) and WORDING not in str(vars(entry)) for entry in entries)


def test_session_cookie_flags(tmp_path: Path, db_factory) -> None:
    settings = make_settings(tmp_path, public_url="https://contracts.example.test")
    migrate(settings)
    add_user(db_factory)
    with TestClient(create_app_for(settings), base_url="https://testserver") as client:
        client.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        response = client.post(
            "/api/auth/login", json={"login": "manager", "password": "correct-horse-battery"}
        )
        cookie = response.headers["set-cookie"]
        assert re.search(r"contract_review_session=\S+;", cookie)
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            assert flag.lower() in cookie.lower(), flag
