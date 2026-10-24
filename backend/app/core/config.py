"""Настройки машины. Читаются из переменных окружения и файла .env в корне репозитория.

Всё, что не зависит от машины (промпт, реквизиты, подключения ИИ), хранится в базе.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

# Значения-заглушки из .env.example, с которыми запускаться нельзя.
PLACEHOLDER_SECRETS = {"change-me", "changeme", "secret", "password"}


class Settings(BaseSettings):
    # Переменные окружения важнее .env. В Docker файла .env в образе нет, значения задаёт compose.
    model_config = SettingsConfigDict(env_file=REPO_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # Без DATABASE_URL — локальная SQLite для разработки без Docker.
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'dev.db').as_posix()}"
    app_secret_key: str = Field(min_length=32)
    # Ключ Fernet для шифрования ключей подключений ИИ (app/core/crypto.py).
    encryption_key: str = ""
    # Команда или путь к Codex CLI. В образе с INSTALL_CODEX=true — /usr/local/bin/codex.
    codex_bin: str = "codex"
    public_url: str = "http://localhost:8080"
    data_dir: Path = BACKEND_DIR / "data"
    # Собранный интерфейс. Без значения берётся frontend/dist, если он есть.
    static_dir: Path | None = None
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    initial_admin_login: str = ""
    initial_admin_password: str = ""
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)
    # Срок жизни проверки и лимит файла — значения по умолчанию, пока администратор не задал свои
    # в разделе «Настройки» (app/runtime.py).
    check_ttl_hours: int = Field(default=24, ge=1, le=24 * 30)
    max_upload_mb: int = Field(default=50, ge=1, le=200)
    log_level: str = "INFO"
    # Схема API и Swagger (/api/docs, /api/openapi.json) — только для разработки: в продукте они видны
    # без входа и перечисляют все ручки, включая админку.
    api_docs: bool = False

    # mode="before": проверка на заглушку раньше проверки длины, чтобы сообщение было понятным.
    @field_validator("app_secret_key", mode="before")
    @classmethod
    def _reject_placeholder(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in PLACEHOLDER_SECRETS:
            raise ValueError(
                "APP_SECRET_KEY — заглушка из .env.example, задайте случайную строку длиной от 32 символов"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_level(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.lower().startswith("https://")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def resolved_static_dir(self) -> Path | None:
        if self.static_dir is not None:
            return self.static_dir if self.static_dir.is_dir() else None
        default = REPO_DIR / "frontend" / "dist"
        return default if default.is_dir() else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
