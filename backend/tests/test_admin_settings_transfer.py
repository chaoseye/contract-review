"""Этап 5: данные организации с картинками, сверка на экране проверки, выгрузка и загрузка настроек."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.api.middleware import CSRF_HEADER, CSRF_HEADER_VALUE
from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import LlmProfile, Organization, PromptVersion, User, UserRole
from tests.conftest import add_user, make_settings, migrate
from tests.test_analysis_job import uploaded
from tests.test_checks_api import login

KEY = "sk-transfer-key-4242"
REQUISITES = {
    "inn": "7700 0000 0156",
    "ogrnip": "323770000000017",
    "postal_address": "100000, г. Пример, ул. Тестовая, д. 2, а/я 1",
    "bik": "044525999",
    "account": "40802810500000000001",
    "email": "orders@example.ru",
}
ORGANIZATION = {
    "full_name": "Индивидуальный предприниматель Петров Пётр Петрович",
    "short_name": "ИП Петров Пётр Петрович",
    "signer": "П.П. Петров",
    "representative": "",
    "basis": "",
    "default_city": "г. Пример",
    "footer_text": "100000, г. Пример, тел. +7 900 000-00-00",
    "requisites": REQUISITES,
}


def make_settings_with_key(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path, encryption_key=Fernet.generate_key().decode())
    migrate(settings)
    return settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings_with_key(tmp_path)


@pytest.fixture
def admin(client: TestClient, db_factory) -> int:
    user_id = add_user(db_factory, login="admin", role=UserRole.ADMIN)
    login(client, "admin")
    return user_id


def image(kind: str = "PNG", size: tuple[int, int] = (80, 40)) -> bytes:
    buffer = io.BytesIO()
    mode = "RGBA" if kind == "PNG" else "RGB"
    canvas = Image.new(
        mode, (size[0] + 20, size[1] + 20), (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255)
    )
    canvas.paste(Image.new(mode, size, (20, 40, 160, 255) if mode == "RGBA" else (20, 40, 160)), (10, 10))
    canvas.save(buffer, format=kind)
    return buffer.getvalue()


def put_image(client: TestClient, kind: str, data: bytes, name: str = "image.png"):
    return client.put(f"/api/admin/organization/assets/{kind}", files={"file": (name, data, "image/png")})


def test_organization_fields_and_requisites(client: TestClient, admin: int) -> None:
    empty = client.get("/api/admin/organization").json()
    assert empty["full_name"] == "" and empty["ready_for_protocol"] is False
    assert empty["assets"] == {"signature": False, "stamp": False, "header": False}
    assert empty["opaque_background"] == {"signature": False, "stamp": False}

    bad_inn = client.put("/api/admin/organization", json={**ORGANIZATION, "requisites": {"inn": "12345"}})
    assert bad_inn.status_code == 400 and bad_inn.json()["detail"] == "ИНН: нужно 10 или 12 цифр."
    bad_key = client.put("/api/admin/organization", json={**ORGANIZATION, "requisites": {"kpp": "1"}})
    assert bad_key.status_code == 400 and "Неизвестные реквизиты" in bad_key.json()["detail"]
    bad_email = client.put("/api/admin/organization", json={**ORGANIZATION, "requisites": {"email": "нет"}})
    assert bad_email.status_code == 400
    bad_domain = client.put(
        "/api/admin/organization", json={**ORGANIZATION, "requisites": {"email_domains": "почта"}}
    )
    assert bad_domain.status_code == 400 and "не домен" in bad_domain.json()["detail"]
    domains = client.put(
        "/api/admin/organization",
        json={**ORGANIZATION, "requisites": {"email_domains": "@Example.ru; почта.рф"}},
    )
    assert domains.json()["requisites"] == {"email_domains": "example.ru, почта.рф"}

    saved = client.put("/api/admin/organization", json=ORGANIZATION).json()
    assert saved["ready_for_protocol"] is True
    # Числа хранятся без пробелов, пустые реквизиты не хранятся.
    assert saved["requisites"]["inn"] == "770000000156"
    cleared = client.put(
        "/api/admin/organization", json={**ORGANIZATION, "requisites": {**REQUISITES, "email": " "}}
    ).json()
    assert "email" not in cleared["requisites"]


def test_organization_images_and_preview(client: TestClient, admin: int, db_factory) -> None:
    assert client.get("/api/admin/organization/assets/stamp").status_code == 404
    assert put_image(client, "stamp", b"not an image").status_code == 400
    assert put_image(client, "logo", image()).status_code == 422
    big = put_image(client, "header", b"\x89PNG" + b"0" * (5 * 1024 * 1024 + 1))
    assert big.status_code == 400 and "5 МБ" in big.json()["detail"]

    stamp = image()
    body = put_image(client, "stamp", stamp).json()
    assert body["assets"]["stamp"] is True
    signature_body = put_image(client, "signature", image("JPEG"), "sign.jpg")
    assert signature_body.status_code == 200
    # Белый фон JPEG в протоколе станет прозрачным — предупреждать не о чем.
    assert signature_body.json()["opaque_background"] == {"signature": False, "stamp": False}

    raw = client.get("/api/admin/organization/assets/stamp")
    assert raw.content == stamp and raw.headers["content-type"] == "image/png"
    prepared = client.get("/api/admin/organization/assets/stamp", params={"prepared": True})
    with Image.open(io.BytesIO(prepared.content)) as picture:
        # Как в протоколе: без пустых полей, повёрнута, полупрозрачна.
        assert picture.mode == "RGBA" and picture.size != (100, 60)
        assert picture.getchannel("A").getextrema()[1] < 255
    signature = client.get("/api/admin/organization/assets/signature")
    assert signature.headers["content-type"] == "image/jpeg"
    prepared = client.get("/api/admin/organization/assets/signature", params={"prepared": True})
    assert prepared.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(prepared.content)) as picture:
        # Бумага стала прозрачной и обрезана: осталась только «подпись» 80 × 40.
        assert picture.mode == "RGBA" and picture.width < 90 and picture.height < 50

    # Фото на серой бумаге: фон останется и закроет линию подписи — администратор видит предупреждение.
    buffer = io.BytesIO()
    Image.new("RGB", (100, 60), (190, 185, 175)).save(buffer, format="JPEG")
    assert put_image(client, "signature", buffer.getvalue(), "photo.jpg").json()["opaque_background"] == {
        "signature": True,
        "stamp": False,
    }
    assert client.get("/api/admin/organization").json()["opaque_background"]["signature"] is True

    assert client.delete("/api/admin/organization/assets/stamp").json()["assets"]["stamp"] is False
    with db_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None and organization.stamp_png is None and organization.signature_png


def test_check_detail_shows_requisites_check(
    client: TestClient, admin: int, db_factory, settings, tmp_path
) -> None:
    check_id = uploaded(client, db_factory, settings, tmp_path)
    assert client.get(f"/api/checks/{check_id}").json()["requisites"]["status"] == "skipped"
    client.put("/api/admin/organization", json=ORGANIZATION)
    # Правка карточки видна сразу: сверка считается при каждом открытии проверки.
    requisites = client.get(f"/api/checks/{check_id}").json()["requisites"]
    assert requisites["status"] == "not_found"
    assert "не нашли в договоре" in requisites["warnings"][0]


# --- перенос --------------------------------------------------------------------------------------


def fill(client: TestClient) -> None:
    client.put("/api/admin/organization", json=ORGANIZATION)
    put_image(client, "signature", image())
    put_image(client, "stamp", image())
    client.post("/api/admin/prompts", json={"text": "Вторая версия промпта", "comment": "для переноса"})
    client.post("/api/admin/prompts", json={"text": "Черновик", "activate": False})
    response = client.post(
        "/api/admin/llm-profiles",
        json={
            "name": "RouterAI",
            "kind": "openai_compat",
            "base_url": "https://routerai.ru/api/v1",
            "model": "vendor/model",
            "api_key": KEY,
            "params": {"russia_only": True, "timeout_seconds": 200},
            "activate": True,
        },
    )
    assert response.status_code == 201, response.text
    client.post(
        "/api/admin/llm-profiles",
        json={
            "name": "Ollama",
            "kind": "openai_compat",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen",
        },
    )
    client.post(
        "/api/admin/llm-profiles",
        json={
            "name": "Claude",
            "kind": "anthropic",
            "model": "claude-opus-5-5",
            "api_key": "sk-ant-transfer-0001",
            "params": {"effort": "high"},
        },
    )
    template = client.get("/api/admin/protocol-template").json()["template"]
    client.put("/api/admin/protocol-template", json={**template, "title": "ПРОТОКОЛ"})
    client.put("/api/admin/settings", json={"check_ttl_hours": 48, "max_upload_mb": 20})


def export(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/admin/export")
    assert response.status_code == 200
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    return json.loads(response.content)


def test_export_contains_settings_without_secrets(client: TestClient, admin: int) -> None:
    fill(client)
    data = export(client)
    text = json.dumps(data, ensure_ascii=False)
    assert data["format"] == "contract-review-settings" and data["version"] == 1
    assert KEY not in text and "key_hint" not in text and "password" not in text
    assert "admin" not in text  # пользователей в файле нет
    assert [item["is_active"] for item in data["prompt_versions"]] == [False, True, False]
    assert {item["name"] for item in data["llm_profiles"]} == {"RouterAI", "Ollama", "Claude"}
    assert "sk-ant-transfer" not in text
    assert data["organization"]["requisites"]["inn"] == "770000000156"
    assert data["organization"]["images"]["header"] is None and data["organization"]["images"]["stamp"]
    assert data["protocol_template"]["title"] == "ПРОТОКОЛ"
    assert data["limits"] == {"check_ttl_hours": 48, "max_upload_mb": 20}


def test_import_into_new_installation(client: TestClient, admin: int, tmp_path: Path) -> None:
    fill(client)
    data = export(client)
    target_settings = make_settings_with_key(tmp_path / "target")
    target_factory = make_sessionmaker(make_engine(target_settings.database_url))
    add_user(target_factory, login="root", role=UserRole.ADMIN)
    with TestClient(create_app(target_settings)) as target:
        target.headers[CSRF_HEADER] = CSRF_HEADER_VALUE
        login(target, "root")
        files = {"file": ("settings.json", json.dumps(data).encode("utf-8"), "application/json")}
        response = target.post("/api/admin/import", files=files)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["prompt_versions_added"] == 3
        assert sorted(report["profiles_added"]) == ["Claude", "Ollama", "RouterAI"]
        # Ключи в файл не входят: у RouterAI и Anthropic ключ вводится заново, локальной Ollama он не нужен.
        assert sorted(report["profiles_need_key"]) == ["Claude", "RouterAI"]
        assert report["profile_active"] == "RouterAI"
        assert report["organization"] and report["protocol_template"] and report["limits"]

        versions = target.get("/api/admin/prompts").json()
        assert len(versions) == 3 and [version["is_active"] for version in versions] == [False, True, False]
        assert target.get(f"/api/admin/prompts/{versions[1]['id']}").json()["text"] == "Вторая версия промпта"
        organization = target.get("/api/admin/organization").json()
        assert organization["assets"] == {"signature": True, "stamp": True, "header": False}
        assert organization["requisites"] == {**REQUISITES, "inn": "770000000156"}
        assert target.get("/api/admin/protocol-template").json()["template"]["title"] == "ПРОТОКОЛ"
        assert target.get("/api/limits").json() == {"check_ttl_hours": 48, "max_upload_mb": 20}

        # Повторная загрузка того же файла ничего не дублирует.
        again = target.post("/api/admin/import", files=files).json()
        assert again["prompt_versions_added"] == 0 and sorted(again["profiles_updated"]) == [
            "Claude",
            "Ollama",
            "RouterAI",
        ]
        assert len(target.get("/api/admin/prompts").json()) == 3
    with target_factory() as db:
        assert [user.login for user in db.scalars(select(User))] == ["root"]
        routerai = db.scalar(select(LlmProfile).where(LlmProfile.name == "RouterAI"))
        assert routerai is not None and routerai.api_key_enc is None
        assert routerai.params["timeout_seconds"] == 200 and routerai.params["russia_only"] is True
        claude = db.scalar(select(LlmProfile).where(LlmProfile.name == "Claude"))
        assert claude is not None and claude.kind == "anthropic" and claude.params["effort"] == "high"
        assert claude.base_url == "https://api.anthropic.com"


def test_import_with_another_server_drops_saved_key(client: TestClient, admin: int, db_factory) -> None:
    # Чужой или испорченный файл менял адрес подключения, и следующий анализ слал ключ туда.
    fill(client)
    data = export(client)
    for item in data["llm_profiles"]:
        if item["name"] == "Claude":
            item["base_url"] = "https://anthropic.evil.test"
        if item["name"] == "RouterAI":
            item["base_url"] = "https://routerai.ru/api/v2"  # тот же сервер — ключ остаётся
    files = {"file": ("settings.json", json.dumps(data).encode("utf-8"), "application/json")}
    response = client.post("/api/admin/import", files=files)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["profiles_need_key"] == ["Claude"]
    assert any("«Claude»" in note and "ключ сброшен" in note for note in report["notes"])
    with db_factory() as db:
        claude = db.scalar(select(LlmProfile).where(LlmProfile.name == "Claude"))
        routerai = db.scalar(select(LlmProfile).where(LlmProfile.name == "RouterAI"))
        assert claude is not None and claude.api_key_enc is None and claude.params["key_hint"] == ""
        assert routerai is not None and routerai.api_key_enc is not None
        assert routerai.base_url == "https://routerai.ru/api/v2"


def test_import_rejects_bad_files_without_changes(client: TestClient, admin: int, db_factory) -> None:
    def post(content: bytes):
        return client.post("/api/admin/import", files={"file": ("s.json", content, "application/json")})

    assert post(b"not json").status_code == 400
    wrong = post(json.dumps({"format": "other", "version": 1}).encode())
    assert wrong.status_code == 400 and "не файл настроек" in wrong.json()["detail"]
    newer = post(json.dumps({"format": "contract-review-settings", "version": 99}).encode())
    assert newer.status_code == 400

    bad_image = {
        "format": "contract-review-settings",
        "version": 1,
        "prompt_versions": [{"text": "Промпт из файла", "is_active": True}],
        "organization": {**ORGANIZATION, "images": {"stamp": "!!!"}},
    }
    response = post(json.dumps(bad_image).encode())
    assert response.status_code == 400 and "Настройки не загружены" in response.json()["detail"]
    bad_requisites = {**bad_image, "organization": {**ORGANIZATION, "requisites": {"inn": "1"}}}
    assert post(json.dumps(bad_requisites).encode()).status_code == 400
    with db_factory() as db:
        # Ничего из отвергнутых файлов не применилось.
        assert db.scalar(select(Organization)) is None
        assert all(version.text != "Промпт из файла" for version in db.scalars(select(PromptVersion)))
