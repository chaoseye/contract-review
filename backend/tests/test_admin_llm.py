"""Этап 5: подключения ИИ, вход в Codex и прогон черновика промпта через админку — без сети.

Провайдеры openai_compat и anthropic отвечают через httpx2.MockTransport (app.state.llm_http_client),
Codex — поддельный tests/fake_codex.py (app.state.codex_command).
"""

import sys
import threading
from pathlib import Path
from typing import Any

import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analysis import profiles
from app.analysis.providers.base import AnalysisRequest, AnalysisResult, ProviderConfigError
from app.core.config import Settings
from app.models import AuditEntry, Check, LlmProfile, UserRole
from tests.analysis_helpers import FakeProvider
from tests.conftest import add_user, make_settings, migrate
from tests.test_analysis_job import contract_answer, uploaded
from tests.test_anthropic_api import MODEL, page, stream, text
from tests.test_checks_api import login, run_next_job
from tests.test_openai_compat import completion

KEY = "sk-test-llm-key-0001"
BASE_URL = "https://llm.example.test/v1"
FAKE_CODEX = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]
MODELS = {
    "object": "list",
    "data": [
        {"id": "vendor/model", "name": "Модель", "context_length": 200000},
        {"id": "vendor/other", "name": "Другая"},
        {"id": "vendor/image", "architecture": {"output_modalities": ["image"]}},
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Ключ шифрования нужен, чтобы сохранять ключи API подключений.
    settings = make_settings(tmp_path, encryption_key=Fernet.generate_key().decode())
    migrate(settings)
    return settings


class Server:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.host == "api.anthropic.com":
            return self.anthropic(request)
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx2.Response(401, json={"error": {"message": "Invalid API key"}})
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json=MODELS)
        return httpx2.Response(200, json=completion('{"ok": true, "answer": "готов"}'))

    def anthropic(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("x-api-key") != KEY:
            error = {"type": "authentication_error", "message": "invalid x-api-key"}
            return httpx2.Response(401, json={"type": "error", "error": error})
        if request.url.path == "/v1/models":
            return page(MODEL)
        if request.url.path.startswith("/v1/models/"):
            return httpx2.Response(200, json=MODEL)
        return stream(text('{"ok": true, "answer": "готов"}'))


@pytest.fixture
def server(app) -> Server:
    fake = Server()
    app.state.llm_http_client = httpx2.Client(transport=httpx2.MockTransport(fake))
    app.state.codex_command = FAKE_CODEX
    return fake


@pytest.fixture
def admin(client: TestClient, db_factory, server) -> int:
    user_id = add_user(db_factory, login="admin", role=UserRole.ADMIN)
    login(client, "admin")
    return user_id


def create(client: TestClient, **fields: Any) -> dict[str, Any]:
    body = {
        "name": "Тестовый",
        "kind": "openai_compat",
        "base_url": BASE_URL,
        "model": "vendor/model",
        **fields,
    }
    response = client.post("/api/admin/llm-profiles", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_presets(client: TestClient, admin: int) -> None:
    presets = {item["id"]: item for item in client.get("/api/admin/llm-presets").json()}
    assert presets["routerai"]["russia_only"] is False and presets["routerai"]["needs_key"] is True
    assert presets["ollama"]["needs_key"] is False
    assert presets["openai"]["kind"] == "openai_compat" and presets["openai"]["effort"] is None
    assert presets["anthropic"] == {
        "id": "anthropic",
        "title": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "needs_key": True,
        "russia_only": False,
        "kind": "anthropic",
        "effort": "high",
    }


def test_anthropic_profile(client: TestClient, admin: int, server: Server) -> None:
    unsaved = client.post("/api/admin/llm-models", json={"kind": "anthropic", "api_key": KEY}).json()
    assert [item["id"] for item in unsaved["models"]] == ["claude-opus-5-5"]
    assert unsaved["models"][0]["max_output_tokens"] == 128_000
    without_key = client.post("/api/admin/llm-models", json={"kind": "anthropic"})
    assert without_key.status_code == 400 and "ключ" in without_key.json()["detail"]
    codex = client.post("/api/admin/llm-models", json={"kind": "codex_cli"})
    assert codex.status_code == 400 and "Codex" in codex.json()["detail"]
    json_mode = client.post(
        "/api/admin/llm-profiles",
        json={
            "name": "X",
            "kind": "anthropic",
            "model": "claude-opus-5-5",
            "params": {"response_format": "json_object"},
        },
    )
    assert json_mode.status_code == 400 and "JSON-режима" in json_mode.json()["detail"]

    profile = create(
        client, kind="anthropic", base_url="", model="claude-opus-5-5", api_key=KEY, params={"effort": "high"}
    )
    assert profile["base_url"] == "https://api.anthropic.com"
    assert profile["params"]["effort"] == "high"
    assert client.post("/api/admin/llm-models", json={"profile_id": profile["id"]}).json()["total"] == 1

    report = client.post(f"/api/admin/llm-profiles/{profile['id']}/test").json()
    assert report["ok"] is True, report
    assert report["response_format"] == "json_schema"
    assert report["model_info"]["supported_parameters"][0] == "structured_outputs"
    assert report["cost_rub"] is None
    saved = client.get("/api/admin/llm-profiles").json()[0]
    assert saved["kind"] == "anthropic" and saved["model_info"]["max_output_tokens"] == 128_000
    assert KEY not in str(saved)


def test_profile_crud_key_is_never_returned(client: TestClient, admin: int, db_factory) -> None:
    created = create(client, api_key=KEY, params={"timeout_seconds": 120, "key_hint": "hack"})
    assert created["is_active"] is True  # первое подключение становится активным само
    assert created["has_key"] is True and created["key_hint"] == "0001"
    assert created["params"]["timeout_seconds"] == 120
    assert KEY not in str(client.get("/api/admin/llm-profiles").json())

    duplicate = client.post("/api/admin/llm-profiles", json={"name": "Тестовый", "kind": "openai_compat"})
    assert duplicate.status_code == 400 and "уже есть" in duplicate.json()["detail"]
    bad = client.post(
        "/api/admin/llm-profiles",
        json={"name": "X", "kind": "openai_compat", "base_url": "ftp://x", "model": "m"},
    )
    assert bad.status_code == 400 and "http://" in bad.json()["detail"]
    codex = client.post(
        "/api/admin/llm-profiles",
        json={"name": "Codex", "kind": "codex_cli", "params": {"russia_only": True}},
    )
    assert codex.status_code == 400 and "Codex" in codex.json()["detail"]

    second = create(client, name="Второй", api_key=KEY)
    assert second["is_active"] is False
    renamed = client.patch(
        f"/api/admin/llm-profiles/{second['id']}",
        json={"name": "Запасной", "api_key": "", "model": "vendor/other"},
    ).json()
    assert (renamed["name"], renamed["has_key"], renamed["key_hint"], renamed["model"]) == (
        "Запасной",
        False,
        "",
        "vendor/other",
    )
    clash = client.patch(f"/api/admin/llm-profiles/{second['id']}", json={"name": "Тестовый"})
    assert clash.status_code == 400

    activated = client.post(f"/api/admin/llm-profiles/{second['id']}/activate").json()
    assert activated["is_active"] is True
    listed = {item["name"]: item["is_active"] for item in client.get("/api/admin/llm-profiles").json()}
    assert listed == {"Запасной": True, "Тестовый": False}

    assert client.delete(f"/api/admin/llm-profiles/{second['id']}").status_code == 204
    assert [item["name"] for item in client.get("/api/admin/llm-profiles").json()] == ["Тестовый"]
    assert client.delete(f"/api/admin/llm-profiles/{second['id']}").status_code == 404
    with db_factory() as db:
        actions = [row.action for row in db.scalars(select(AuditEntry).order_by(AuditEntry.id))]
        details = " ".join(row.details or "" for row in db.scalars(select(AuditEntry)))
    assert actions == ["llm_create", "llm_create", "llm_update", "llm_activate", "llm_delete"]
    assert KEY not in details and "ключ удалён" in details


def test_connection_check_saves_model_info(
    client: TestClient, admin: int, server: Server, db_factory
) -> None:
    profile = create(client, api_key=KEY)
    report = client.post(f"/api/admin/llm-profiles/{profile['id']}/test").json()
    assert report["ok"] is True, report
    assert report["model_info"]["id"] == "vendor/model" and report["model_info"]["context_length"] == 200000
    assert report["input_tokens"] == 1000 and report["response_format"]
    # Договор в проверку связи не уходит: только короткий запрос.
    assert all("ДОГОВОР" not in request.content.decode("utf-8", "replace") for request in server.requests)
    saved = client.get("/api/admin/llm-profiles").json()[0]
    assert saved["model_info"]["id"] == "vendor/model"

    client.patch(f"/api/admin/llm-profiles/{profile['id']}", json={"api_key": "sk-wrong-key-9999"})
    failed = client.post(f"/api/admin/llm-profiles/{profile['id']}/test").json()
    assert failed["ok"] is False and failed["detail"] == "Invalid API key"
    with db_factory() as db:
        tests = [row.details for row in db.scalars(select(AuditEntry).where(AuditEntry.action == "llm_test"))]
    assert tests == ["«Тестовый»: связь есть", "«Тестовый»: ошибка"]


def test_models_for_unsaved_and_saved_profile(client: TestClient, admin: int) -> None:
    listed = client.post("/api/admin/llm-models", json={"base_url": BASE_URL, "api_key": KEY}).json()
    assert [item["id"] for item in listed["models"]] == ["vendor/model", "vendor/other"]
    found = client.post(
        "/api/admin/llm-models", json={"base_url": BASE_URL, "api_key": KEY, "search": "друг"}
    )
    assert [item["id"] for item in found.json()["models"]] == ["vendor/other"]
    denied = client.post("/api/admin/llm-models", json={"base_url": BASE_URL, "api_key": "sk-bad"})
    assert denied.status_code == 400 and "Invalid API key" in denied.json()["detail"]
    assert client.post("/api/admin/llm-models", json={"base_url": "file:///etc"}).status_code == 400

    profile = create(client, api_key=KEY)
    # Сохранённый ключ подставляется сам, в браузер он не возвращается.
    saved = client.post("/api/admin/llm-models", json={"profile_id": profile["id"]}).json()
    assert saved["total"] == 2
    # Тот же сервер с другим путём — ключ подставляется.
    same = client.post(
        "/api/admin/llm-models",
        json={"profile_id": profile["id"], "base_url": "https://LLM.example.test:443/api"},
    )
    assert same.status_code == 200


def test_saved_key_never_goes_to_another_server(
    client: TestClient, admin: int, server: Server, db_factory
) -> None:
    # Опечатка в адресе отправила бы ключ чужому серверу.
    profile = create(client, api_key=KEY)
    before = len(server.requests)
    listed = client.post(
        "/api/admin/llm-models",
        json={"profile_id": profile["id"], "base_url": "https://evil.example.test/v1"},
    )
    assert listed.status_code == 400 and "другой сервер" in listed.json()["detail"]
    assert len(server.requests) == before
    # С ключом из формы — можно: администратор сам ввёл его для этого адреса.
    typed = client.post(
        "/api/admin/llm-models",
        json={"profile_id": profile["id"], "base_url": "https://evil.example.test/v1", "api_key": KEY},
    )
    assert typed.status_code == 200 and server.requests[-1].url.host == "evil.example.test"

    moved = client.patch(
        f"/api/admin/llm-profiles/{profile['id']}", json={"base_url": "https://evil.example.test/v1"}
    )
    assert moved.status_code == 400 and "другой сервер" in moved.json()["detail"]
    with db_factory() as db:
        assert db.get(LlmProfile, profile["id"]).base_url == BASE_URL
    path_only = client.patch(
        f"/api/admin/llm-profiles/{profile['id']}", json={"base_url": "https://llm.example.test/api/v1"}
    )
    assert path_only.status_code == 200 and path_only.json()["has_key"] is True
    with_key = client.patch(
        f"/api/admin/llm-profiles/{profile['id']}",
        json={"base_url": "https://other.example.test/v1", "api_key": KEY},
    )
    assert with_key.status_code == 200 and with_key.json()["base_url"] == "https://other.example.test/v1"


def test_same_server() -> None:
    assert profiles.same_server("https://routerai.ru/api/v1", "https://ROUTERAI.ru:443/v2")
    assert profiles.same_server("http://localhost:11434/v1", "http://localhost:11434")
    assert not profiles.same_server("https://routerai.ru/api/v1", "https://routerai.ru.evil.test/api/v1")
    assert not profiles.same_server("https://routerai.ru", "http://routerai.ru")
    assert not profiles.same_server("http://localhost:11434", "http://localhost:8000")
    assert not profiles.same_server("https://a.test:bad/v1", "https://a.test/v1")
    # IPv6, пользователь в адресе, порт 0 и точка в конце имени.
    assert profiles.same_server("http://[::1]/v1", "http://[::1]:80/api")
    assert not profiles.same_server("http://[::1]:8080/v1", "http://[::1]/v1")
    assert profiles.same_server("https://user:pass@llm.example.test/v1", "https://llm.example.test")
    assert not profiles.same_server("https://llm.example.test@evil.test/v1", "https://llm.example.test/v1")
    assert not profiles.same_server("https://llm.example.test:0/v1", "https://llm.example.test/v1")
    assert not profiles.same_server("https://llm.example.test./v1", "https://llm.example.test/v1")


def test_codex_device_login(client: TestClient, admin: int, app, settings: Settings) -> None:
    status = client.get("/api/admin/codex").json()
    assert status["installed"] is True and status["logged_in"] is False and status["login"] is None
    started = client.post("/api/admin/codex/login").json()
    assert started["login"]["url"] == "https://auth.openai.com/codex/device"
    assert started["login"]["code"] == "ABCD-12345"
    app.state.codex_login.join(10)
    done = client.get("/api/admin/codex").json()
    assert done["logged_in"] is True and done["login"]["state"] == "done"
    assert (profiles.codex_home(settings) / "auth.json").exists()
    out = client.post("/api/admin/codex/logout").json()
    assert out["logged_in"] is False and out["login"] is None


def test_codex_not_installed(client: TestClient, admin: int, app) -> None:
    app.state.codex_command = ["contract-review-no-such-codex-binary"]
    status = client.get("/api/admin/codex").json()
    assert status["installed"] is False and "Codex" in status["message"]
    assert client.post("/api/admin/codex/login").status_code == 400


# --- прогон черновика промпта -------------------------------------------------------------------


class SlowProvider(FakeProvider):
    """Подделка, которая ждёт разрешения: так видно, что второй прогон не начинается, пока идёт первый."""

    def __init__(self, *responses: Any) -> None:
        super().__init__(*responses)
        self.release = threading.Event()

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        self.release.wait(10)
        return super().analyze(request)


def test_prompt_draft_run(
    client: TestClient, admin: int, app, db_factory, settings, tmp_path, monkeypatch
) -> None:
    create(client, api_key=KEY)
    fake = SlowProvider(contract_answer())
    monkeypatch.setattr(profiles, "build_provider", lambda profile, settings, **kwargs: fake)
    # Анализ после чтения не нужен: прогон черновика идёт на прочитанном договоре.
    check_id = uploaded(client, db_factory, settings, tmp_path)
    with db_factory() as db:
        before = db.get(Check, check_id)
        assert before is not None and before.result is None

    started = client.post("/api/admin/prompts/test", json={"text": "Черновик промпта", "check_id": check_id})
    assert started.status_code == 202, started.text
    run = started.json()
    assert run["status"] == "running" and run["profile"] == "Тестовый"
    busy = client.post("/api/admin/prompts/test", json={"text": "Ещё один", "check_id": check_id})
    assert busy.status_code == 409
    fake.release.set()
    app.state.prompt_runs.join(run["id"], 10)

    finished = client.get(f"/api/admin/prompts/test/{run['id']}").json()
    assert finished["status"] == "done", finished
    assert finished["result"]["answer"]["risks"][0]["id"] == "R1"
    assert finished["usage"]["input_tokens"] == 1000
    assert fake.requests[0].system.startswith("Черновик промпта")
    # Прогон ничего не сохраняет в проверке, а в журнале — только числа.
    with db_factory() as db:
        after = db.get(Check, check_id)
        assert after is not None and after.result is None
        entry = db.scalar(select(AuditEntry).where(AuditEntry.action == "prompt_test"))
        assert entry is not None and entry.check_id == check_id and entry.tokens_in == 1000
        assert entry.details == "черновик, подключение «Тестовый»: готово"
    # Проверку удалили — результат прогона тоже недоступен.
    assert client.delete(f"/api/checks/{check_id}").status_code == 204
    assert client.get(f"/api/admin/prompts/test/{run['id']}").status_code == 404
    assert client.get(f"/api/admin/prompts/test/{run['id']}").status_code == 404


def test_prompt_draft_run_errors(
    client: TestClient, admin: int, app, db_factory, settings, tmp_path, monkeypatch
) -> None:
    check_id = uploaded(client, db_factory, settings, tmp_path)
    no_profile = client.post("/api/admin/prompts/test", json={"text": "x", "check_id": check_id})
    assert no_profile.status_code == 409 and "Нет активного подключения" in no_profile.json()["detail"]
    missing = client.post("/api/admin/prompts/test", json={"text": "x", "check_id": "nope"})
    assert missing.status_code == 404

    create(client, api_key=KEY)
    fake = FakeProvider(ProviderConfigError("Ключ API не принят.", "invalid key"))
    monkeypatch.setattr(profiles, "build_provider", lambda profile, settings, **kwargs: fake)
    run = client.post("/api/admin/prompts/test", json={"text": "x", "check_id": check_id}).json()
    app.state.prompt_runs.join(run["id"], 10)
    failed = client.get(f"/api/admin/prompts/test/{run['id']}").json()
    assert failed["status"] == "failed"
    assert failed["error"] == "Прогон не выполнен: Ключ API не принят." and failed["detail"] == "invalid key"

    with db_factory() as db:
        profile = db.scalar(select(LlmProfile))
        assert profile is not None
        other = client.post(
            "/api/admin/prompts/test",
            json={"text": "x", "check_id": check_id, "profile_id": profile.id + 100},
        )
    assert other.status_code == 404


def test_analysis_meta_has_our_role(
    client: TestClient, admin: int, db_factory, settings, tmp_path, monkeypatch
) -> None:
    from tests.test_protocol_api import add_organization

    add_organization(db_factory)
    create(client, api_key=KEY)
    fake = FakeProvider(contract_answer())
    monkeypatch.setattr(profiles, "build_provider", lambda profile, settings, **kwargs: fake)
    check_id = uploaded(client, db_factory, settings, tmp_path)
    run_next_job(db_factory, settings)
    detail = client.get(f"/api/checks/{check_id}").json()
    # В синтетическом договоре нашей организации нет: сверка это говорит, а модель узнаёт без реквизитов.
    assert detail["requisites"]["status"] == "not_found"
    assert detail["analysis"]["our_role"] is None
    assert "найти не удалось" in fake.requests[0].user
    assert "770000000156" not in fake.requests[0].user
