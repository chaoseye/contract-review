from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings, migrate


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_unknown_api_path_is_404_without_static(client: TestClient) -> None:
    assert client.get("/api/unknown").status_code == 404


def test_api_docs_only_for_development(tmp_path: Path) -> None:
    # Схема API открыта без входа и перечисляет все ручки: в продукте выключена.
    production = make_settings(tmp_path / "prod", static_dir=make_static(tmp_path / "prod"))
    migrate(production)
    with TestClient(create_app(production)) as client:
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404
    development = make_settings(tmp_path / "dev", api_docs=True)
    migrate(development)
    with TestClient(create_app(development)) as client:
        assert client.get("/api/docs").status_code == 200
        assert "/api/checks" in client.get("/api/openapi.json").json()["paths"]


def make_static(tmp_path: Path) -> Path:
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>contract-review</title>", encoding="utf-8")
    (static / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (static / "robots.txt").write_text("User-agent: *\nDisallow: /", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("не отдавать", encoding="utf-8")
    return static


def spa_client(tmp_path: Path) -> TestClient:
    settings = make_settings(tmp_path, static_dir=make_static(tmp_path))
    migrate(settings)
    return TestClient(create_app(settings))


def test_spa_serves_index_for_routes_and_files(tmp_path: Path) -> None:
    with spa_client(tmp_path) as client:
        for path in ("/", "/login", "/checks/42"):
            response = client.get(path)
            assert response.status_code == 200
            assert "<title>contract-review</title>" in response.text
            assert response.headers["cache-control"] == "no-cache"
        assert client.get("/assets/app-abc123.js").text == "console.log(1)"
        assert client.get("/robots.txt").text.startswith("User-agent")


def test_spa_answers_head_and_404_for_missing_files(tmp_path: Path) -> None:
    with spa_client(tmp_path) as client:
        assert client.head("/").status_code == 200
        assert client.get("/favicon.ico").status_code == 404
        assert client.get("/assets/old-chunk.js").status_code == 404
        # Точка в последней части пути означает файл: маршруты интерфейса точек не содержат.
        assert client.get("/checks/v1.2").status_code == 404
        assert client.get("/checks/42").status_code == 200


def test_spa_does_not_hide_api_404(tmp_path: Path) -> None:
    with spa_client(tmp_path) as client:
        response = client.get("/api/unknown")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_spa_blocks_path_traversal(tmp_path: Path) -> None:
    with spa_client(tmp_path) as client:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            response = client.get(path)
            assert "не отдавать" not in response.text
