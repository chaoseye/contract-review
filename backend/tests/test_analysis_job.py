"""Задача analyze, API проверок с анализом и команды analyze/evaluate на поддельном провайдере."""

import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import cli, storage
from app.analysis import profiles
from app.analysis import run as run_module
from app.analysis.providers.base import ProviderConfigError, ProviderUnavailableError
from app.core.db import utcnow
from app.jobs import queue, tasks, worker
from app.jobs.cleanup import delete_orphan_files
from app.models import AuditEntry, Check, Job, LlmKind, LlmProfile, UserRole
from tests.analysis_helpers import FakeProvider, answer, risk
from tests.conftest import add_user
from tests.docx_builder import make_docx, p
from tests.test_checks_api import login, run_next_job, upload

CONTRACT = (
    "9. ОТВЕТСТВЕННОСТЬ СТОРОН",
    "9.2. В случае просрочки поставки Поставщик уплачивает неустойку в размере 0,5 % от стоимости товара "
    "за каждый день просрочки, но не более 20 %.",
    "9.8. Покупатель вправе удержать сумму неустойки из оплаты.",
)


def contract_answer() -> dict:
    return answer(
        risks=[
            risk(
                "R1",
                block_ids=["B0002"],
                quote="Поставщик уплачивает неустойку в размере 0,5% от стоимости товара за каждый день",
            )
        ],
        obligations=[],
        favorable=[],
    )


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(profiles, "build_provider", lambda profile, settings, **kwargs: fake)
    return fake


def add_profile(db_factory, name: str = "RouterAI") -> None:
    with db_factory() as db:
        db.add(
            LlmProfile(
                name=name,
                kind=LlmKind.OPENAI_COMPAT,
                base_url="https://routerai.ru/api/v1",
                model="vendor/model",
                params={},
                is_active=True,
            )
        )
        db.commit()


def uploaded(client: TestClient, db_factory, settings, tmp_path: Path, **data) -> str:
    docx = make_docx(tmp_path / "contract.docx", "".join(p(text) for text in CONTRACT)).read_bytes()
    response = client.post(
        "/api/checks",
        files={"file": ("Договор с АО Пример.docx", docx, "application/octet-stream")},
        data={"comment": "", **data},
    )
    assert response.status_code == 201, response.text
    check_id = response.json()["id"]
    run_next_job(db_factory, settings)  # чтение
    return check_id


def jobs(db_factory) -> list[tuple[str, str]]:
    with db_factory() as db:
        return [(job.kind, job.status) for job in db.scalars(select(Job).order_by(Job.id))]


def test_full_path_upload_extract_analyze(client, db_factory, settings, tmp_path, provider) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.append(contract_answer())
    check_id = uploaded(client, db_factory, settings, tmp_path, procurement="44fz", comment="предоплата 50%")

    assert jobs(db_factory) == [("extract", "done"), ("analyze", "queued")]
    detail = client.get(f"/api/checks/{check_id}").json()
    # Анализ в очереди — уже analyzing: интерфейс опрашивает сервер по статусу.
    assert detail["status"] == "analyzing"
    assert detail["progress"] == "Ожидает анализа"
    assert detail["procurement"] == "44fz"

    run_next_job(db_factory, settings)  # анализ
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "ready", detail["error"]
    assert detail["progress"] is None
    result = detail["result"]
    assert result["answer"]["risks"][0]["id"] == "R1"
    assert result["risk_checks"]["R1"]["quote_status"] == "exact"
    meta = detail["analysis"]
    assert meta["profile"] == "RouterAI" and meta["model"] == "fake-model"
    assert (meta["input_tokens"], meta["output_tokens"], meta["attempts"]) == (1000, 200, 1)
    assert meta["cost_rub"] == 0.5 and meta["cost_estimated"] is True
    assert len(meta["prompt_fingerprint"]) == 12
    assert jobs(db_factory)[-1] == ("analyze", "done")

    request = provider.requests[0]
    assert "Режим закупки: госконтракт по 44-ФЗ." in request.user
    assert "предоплата 50%" in request.user
    # Имя файла (в нём название контрагента) в модель не уходит.
    assert "АО Пример" not in request.user and "Договор с" not in request.user
    assert "[B0002] (п. 9.2) 9.2. В случае просрочки" in request.user


def test_without_profile_analysis_is_skipped(client, db_factory, settings, tmp_path, provider) -> None:
    add_user(db_factory)
    login(client, "manager")
    check_id = uploaded(client, db_factory, settings, tmp_path)
    assert jobs(db_factory) == [("extract", "done")]
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "extracted"
    assert "подключение ИИ не настроено" in detail["analysis"]["skipped"]
    response = client.post(f"/api/checks/{check_id}/analyze")
    assert response.status_code == 409
    assert "не настроено" in response.json()["detail"]
    assert provider.requests == []


def test_unavailable_provider_is_retried_then_fails(client, db_factory, settings, tmp_path, provider) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    check_id = uploaded(client, db_factory, settings, tmp_path)
    provider.responses.extend(
        ProviderUnavailableError("Ошибка на стороне провайдера ИИ (502).") for _ in range(4)
    )

    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "analyzing"
    assert "повторится автоматически" in detail["progress"]
    assert detail["analysis"]["error"]["type"] == "ProviderUnavailableError"
    with db_factory() as db:
        job = db.scalars(select(Job).where(Job.kind == "analyze")).one()
        assert (job.status, job.attempts, job.max_attempts) == ("queued", 1, 4)
        assert job.run_after > utcnow()

    for _ in range(3):
        with db_factory() as db:
            db.execute(update(Job).where(Job.kind == "analyze").values(run_after=utcnow()))
            db.commit()
        run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"] == "Анализ не выполнен: Ошибка на стороне провайдера ИИ (502)."
    assert len(provider.requests) == 4
    assert jobs(db_factory)[-1] == ("analyze", "failed")
    # Когда повторы кончились, неудачный анализ — в журнале.
    assert failures(db_factory, check_id) == [("vendor/model", detail["error"])]


def failures(db_factory, check_id: str) -> list[tuple[str | None, str | None]]:
    with db_factory() as db:
        entries = db.scalars(
            select(AuditEntry).where(AuditEntry.check_id == check_id, AuditEntry.action == "analysis_failed")
        )
        return [(entry.model, entry.details) for entry in entries]


def test_unexpected_error_after_model_answer_is_not_repeated(
    client, db_factory, settings, tmp_path, provider, monkeypatch
) -> None:
    # Повтор снова отправил бы договор платной модели.
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.extend([contract_answer(), contract_answer()])

    def broken(*args, **kwargs):
        raise RuntimeError("сбой проверки ответа")

    monkeypatch.setattr(run_module, "check_answer", broken)
    check_id = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"] == queue.ANALYSIS_INTERNAL_ERROR_MESSAGE
    assert detail["progress"] is None
    assert len(provider.requests) == 1
    assert jobs(db_factory)[-1] == ("analyze", "failed")
    assert failures(db_factory, check_id) == [("vendor/model", queue.ANALYSIS_INTERNAL_ERROR_MESSAGE)]
    # Документ прочитан: анализ можно запустить снова, файл загружать не нужно.
    assert client.post(f"/api/checks/{check_id}/analyze").status_code == 202


def test_error_before_analysis_start_does_not_leave_check_waiting(
    client, db_factory, settings, tmp_path, provider, monkeypatch
) -> None:
    # Раньше проверка навсегда оставалась «Ожидает анализа» без ошибки.
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")

    def broken(db, check):
        raise KeyError("id")

    monkeypatch.setattr(tasks, "check_info", broken)
    check_id = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert (detail["status"], detail["progress"]) == ("failed", None)
    assert detail["error"] == queue.ANALYSIS_INTERNAL_ERROR_MESSAGE
    assert provider.requests == []
    assert jobs(db_factory)[-1] == ("analyze", "failed")


def make_stale(db_factory, job_id: int) -> None:
    """Задача «зависла»: worker выполнил её, но отметку done записать не смог, — и вернуть её в очередь."""
    with db_factory() as db:
        old = utcnow() - queue.STALE_AFTER - timedelta(seconds=1)
        db.execute(update(Job).where(Job.id == job_id).values(locked_at=old))
        db.commit()
        assert queue.recover_stale(db) == 1
        db.execute(update(Job).where(Job.id == job_id).values(run_after=utcnow()))
        db.commit()


def test_repeat_of_finished_extract_changes_nothing(client, db_factory, settings, tmp_path, provider) -> None:
    # Раньше повтор стирал правки протокола и второй раз платил за анализ.
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.extend([contract_answer(), contract_answer()])
    docx = make_docx(tmp_path / "contract.docx", "".join(p(text) for text in CONTRACT)).read_bytes()
    check_id = upload(client, docx).json()["id"]
    with db_factory() as db:
        extract_id = queue.claim(db, "w").id
    tasks.handle_extract(db_factory, settings, extract_id, check_id)  # итог чтения не записан: «в работе»
    run_next_job(db_factory, settings)  # анализ
    with db_factory() as db:
        db.get(Check, check_id).protocol = {"правка": "менеджера"}
        db.commit()

    make_stale(db_factory, extract_id)
    run_next_job(db_factory, settings)  # повтор чтения
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "ready"
    with db_factory() as db:
        assert db.get(Check, check_id).protocol == {"правка": "менеджера"}
    assert len(provider.requests) == 1
    assert jobs(db_factory) == [("extract", "done"), ("analyze", "done")]


def test_repeat_of_finished_analysis_changes_nothing(
    client, db_factory, settings, tmp_path, provider
) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.extend([contract_answer(), contract_answer()])
    check_id = uploaded(client, db_factory, settings, tmp_path)
    with db_factory() as db:
        analyze_id = queue.claim(db, "w").id
    tasks.handle_analyze(db_factory, settings, analyze_id, check_id)  # итог анализа не записан: «в работе»
    first = client.get(f"/api/checks/{check_id}").json()
    assert first["status"] == "ready"

    make_stale(db_factory, analyze_id)
    run_next_job(db_factory, settings)  # повтор анализа
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "ready" and detail["result"] == first["result"]
    assert len(provider.requests) == 1
    assert jobs(db_factory) == [("extract", "done"), ("analyze", "done")]


def test_parallel_extract_does_not_overwrite_finished_check(
    client, db_factory, settings, tmp_path, provider, monkeypatch
) -> None:
    # Задачу сочли зависшей, пока чтение ещё шло: второе выполнение закончилось раньше первого.
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.extend([contract_answer(), contract_answer()])
    docx = make_docx(tmp_path / "contract.docx", "".join(p(text) for text in CONTRACT)).read_bytes()
    check_id = upload(client, docx).json()["id"]
    with db_factory() as db:
        job_id = queue.claim(db, "A").id
    original = tasks.extract_document
    calls: list[int] = []

    def slow_first(*args, **kwargs):
        document = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            make_stale(db_factory, job_id)
            run_next_job(db_factory, settings)  # второе выполнение чтения — целиком
            run_next_job(db_factory, settings)  # анализ
            with db_factory() as db:
                db.get(Check, check_id).protocol = {"правка": "менеджера"}
                db.commit()
        return document

    monkeypatch.setattr(tasks, "extract_document", slow_first)
    worker.process_job(db_factory, settings, job_id, "extract", check_id)  # первое заканчивает последним
    assert client.get(f"/api/checks/{check_id}").json()["status"] == "ready"
    with db_factory() as db:
        assert db.get(Check, check_id).protocol == {"правка": "менеджера"}
    assert len(provider.requests) == 1
    assert jobs(db_factory) == [("extract", "done"), ("analyze", "done")]


def test_parallel_analysis_keeps_first_result(
    client, db_factory, settings, tmp_path, provider, monkeypatch
) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    provider.responses.extend([contract_answer(), answer(risks=[risk("R9")])])
    check_id = uploaded(client, db_factory, settings, tmp_path)
    with db_factory() as db:
        job_id = queue.claim(db, "A").id
    original = tasks.run_analysis
    calls: list[int] = []

    def slow_first(*args, **kwargs):
        outcome = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            make_stale(db_factory, job_id)
            run_next_job(db_factory, settings)  # второе выполнение анализа — целиком
        return outcome

    monkeypatch.setattr(tasks, "run_analysis", slow_first)
    worker.process_job(db_factory, settings, job_id, "analyze", check_id)
    detail = client.get(f"/api/checks/{check_id}").json()
    # Оба запроса уже ушли к модели, но в проверке — итог того выполнения, что закончилось первым.
    assert detail["status"] == "ready"
    assert [item["id"] for item in detail["result"]["answer"]["risks"]] == ["R9"]
    with db_factory() as db:
        done = db.scalars(select(AuditEntry.action).where(AuditEntry.action == "analysis_done")).all()
    assert len(done) == 1


def test_config_error_fails_without_retry(client, db_factory, settings, tmp_path, provider) -> None:
    add_user(db_factory)
    add_profile(db_factory)
    login(client, "manager")
    check_id = uploaded(client, db_factory, settings, tmp_path)
    provider.responses.append(
        ProviderConfigError("Недостаточно средств на балансе провайдера ИИ (402).", "balance")
    )
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"] == "Анализ не выполнен: Недостаточно средств на балансе провайдера ИИ (402)."
    assert detail["analysis"]["error"]["detail"] == "balance"
    assert jobs(db_factory)[-1] == ("analyze", "done")
    assert failures(db_factory, check_id) == [("vendor/model", detail["error"])]


def test_broken_profile_fails_check(client, db_factory, settings, tmp_path) -> None:
    add_user(db_factory)
    with db_factory() as db:
        # Ключ зашифрован другим ENCRYPTION_KEY: расшифровать его нельзя.
        db.add(
            LlmProfile(
                name="RouterAI",
                kind=LlmKind.OPENAI_COMPAT,
                base_url="https://routerai.ru/api/v1",
                model="m",
                api_key_enc="gAAAAA-broken",
                params={},
                is_active=True,
            )
        )
        db.commit()
    login(client, "manager")
    check_id = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    assert detail["status"] == "failed"
    assert detail["error"].startswith("Анализ не выполнен: ")
    assert "ENCRYPTION_KEY" in detail["error"]


def test_reanalysis_endpoint(app, db_factory, settings, tmp_path, provider) -> None:
    add_user(db_factory)
    add_user(db_factory, login="admin", role=UserRole.ADMIN)
    add_user(db_factory, login="other")
    add_profile(db_factory)
    provider.responses.extend([contract_answer(), contract_answer()])
    with TestClient(app) as owner, TestClient(app) as admin, TestClient(app) as other:
        for client, name in ((owner, "manager"), (admin, "admin"), (other, "other")):
            client.headers["X-Requested-With"] = "contract-review"
            login(client, name)
        check_id = uploaded(owner, db_factory, settings, tmp_path)
        assert owner.post(f"/api/checks/{check_id}/analyze").json()["detail"] == "Анализ уже идёт."
        run_next_job(db_factory, settings)
        assert owner.get(f"/api/checks/{check_id}").json()["status"] == "ready"

        assert other.post(f"/api/checks/{check_id}/analyze").status_code == 404
        assert admin.post(f"/api/checks/{check_id}/analyze").status_code == 403
        response = owner.post(f"/api/checks/{check_id}/analyze")
        assert response.status_code == 202
        assert response.json()["status"] == "analyzing"
        detail = owner.get(f"/api/checks/{check_id}").json()
        assert detail["result"] is None
        run_next_job(db_factory, settings)
        assert owner.get(f"/api/checks/{check_id}").json()["status"] == "ready"
        assert len(provider.requests) == 2


def test_upload_rejects_unknown_procurement(client, db_factory, tmp_path) -> None:
    add_user(db_factory)
    login(client, "manager")
    docx = make_docx(tmp_path / "c.docx", p("1.1. Текст")).read_bytes()
    response = upload(client, docx)
    assert response.status_code == 201
    bad = client.post(
        "/api/checks",
        files={"file": ("c.docx", docx, "application/octet-stream")},
        data={"procurement": "gov"},
    )
    assert bad.status_code == 400


def test_keep_alive_updates_heartbeat(db_factory, settings) -> None:
    add_user(db_factory)
    with db_factory() as db:
        check = Check(
            owner_id=1, file_name="x", file_kind="docx", file_size=1, expires_at=utcnow() + timedelta(hours=1)
        )
        db.add(check)
        db.flush()
        queue.enqueue(db, "analyze", check.id)
        db.commit()
        job = queue.claim(db, "worker")
        job_id, started = job.id, job.locked_at
    with queue.keep_alive(db_factory, job_id, interval=0.05):
        time.sleep(0.3)
    with db_factory() as db:
        assert db.get(Job, job_id).locked_at > started


def test_cleanup_removes_stale_codex_temp_dirs(db_factory, settings) -> None:
    temp = storage.temp_root(settings)
    old, fresh = temp / "codex-old", temp / "codex-fresh"
    for path in (old, fresh):
        path.mkdir(parents=True)
        (path / "answer.json").write_text("{}", encoding="utf-8")
    hour_ago = time.time() - 7200
    os.utime(old, (hour_ago, hour_ago))
    with db_factory() as db:
        assert delete_orphan_files(db, settings) == 1
    assert not old.exists() and fresh.exists()


# --- команды CLI ----------------------------------------------------------------------------------


def test_cli_analyze_and_evaluate(db_factory, settings, tmp_path, monkeypatch, capsys, provider) -> None:
    add_profile(db_factory)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    contract = make_docx(tmp_path / "contract.docx", "".join(p(text) for text in CONTRACT))
    provider.responses.append(contract_answer())
    out = tmp_path / "result.json"
    cli.main(["analyze", str(contract), "--procurement", "commercial", "--out", str(out)])
    printed = capsys.readouterr().out
    assert "Главные риски:" in printed
    assert (
        "R1 [высокая, стоп-фактор] п. 9.2: Высокая неустойка за просрочку поставки (цитата найдена" in printed
    )
    assert "Согласование без правок: ЗАПРЕЩЕНО" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["result"]["risk_checks"]["R1"]["quote_status"] == "exact"
    assert saved["analysis"]["profile"] == "RouterAI"

    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "name": "проба",
                "rows": [
                    {"n": 1, "clauses": ["9.2"], "mandatory": True},
                    {"n": 2, "clauses": ["9.8"], "mandatory": True},
                    # В ответе модели спецификация — в недостающем.
                    {
                        "n": 3,
                        "clauses": ["Приложение № 1"],
                        "topic": "спецификация",
                        "missing_keywords": ["специф"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metrics = tmp_path / "metrics.json"
    cli.main(["evaluate", str(expected), "--answer", str(out), "--metrics", str(metrics)])
    printed = capsys.readouterr().out
    assert "Найдено строк протокола: 2 из 3 (67%) — порог 80%: нет" in printed
    assert "не найдены: 2" in printed
    assert "не найдена строка 2" in printed
    assert "строка 3 найдена не в рисках (missing[0]), строки протокола для неё нет: спецификация" in printed
    data = json.loads(metrics.read_text(encoding="utf-8"))
    assert data["found_rows"] == [1, 3] and data["mandatory_missed"] == [2]
    assert data["matches"] == {"1": ["R1"], "3": ["missing[0]"]} and data["found_outside_risks"] == [3]
    # В показателях нет текстов договора и ответа модели.
    assert "неустойк" not in metrics.read_text(encoding="utf-8")
    assert len(provider.requests) == 1  # оценка по сохранённому ответу модель не вызывает


def test_cli_analyze_without_profile(db_factory, settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    contract = make_docx(tmp_path / "contract.docx", p("1.1. Текст"))
    with pytest.raises(SystemExit, match="Нет активного подключения"):
        cli.main(["analyze", str(contract)])
