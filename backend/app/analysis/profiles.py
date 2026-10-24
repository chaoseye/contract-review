"""Профили подключений ИИ: хранение, проверка настроек, сборка подключения.

Профилями управляют раздел «Подключения ИИ» в админке (app/api/admin/llm.py) и команды
`python -m app.cli llm-*`. Обе стороны используют функции этого модуля.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import storage
from app.analysis.providers.anthropic_api import AnthropicProvider
from app.analysis.providers.anthropic_api import normalize_base_url as normalize_anthropic_url
from app.analysis.providers.base import CheckReport, ProfileParams, Provider
from app.analysis.providers.codex_cli import CodexCliProvider, resolve_codex_command
from app.analysis.providers.openai_compat import OpenAICompatProvider, supports_country_filter
from app.core.config import Settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models import LlmKind, LlmProfile


class ProfileError(ValueError):
    """Неверные настройки профиля. Текст — для администратора."""


def codex_home(settings: Settings) -> Path:
    return settings.data_dir / "codex"


def profile_params(profile: LlmProfile) -> ProfileParams:
    try:
        return ProfileParams.model_validate(profile.params or {})
    except ValidationError as exc:
        raise ProfileError(
            f"Параметры подключения «{profile.name}» повреждены: {exc.error_count()} ошибок."
        ) from exc


def validate_settings(kind: str, base_url: str, model: str, params: ProfileParams) -> None:
    if params.effort is not None and kind != LlmKind.ANTHROPIC:
        raise ProfileError(
            "Глубина рассуждений (effort) задаётся только у подключения Anthropic. "
            "Для других моделей — в дополнительных полях запроса."
        )
    if kind in (LlmKind.OPENAI_COMPAT, LlmKind.ANTHROPIC):
        if not base_url.startswith(("http://", "https://")):
            raise ProfileError("Адрес API должен начинаться с http:// или https://.")
        if not model:
            raise ProfileError("Укажите модель.")
    if kind == LlmKind.OPENAI_COMPAT:
        if params.russia_only and not supports_country_filter(base_url):
            raise ProfileError("«Только серверы в РФ» работает только с RouterAI.")
    elif kind == LlmKind.ANTHROPIC:
        if params.russia_only:
            raise ProfileError("Anthropic обрабатывает запросы за рубежом: «Только серверы в РФ» недоступна.")
        if params.response_format == "json_object":
            raise ProfileError("У Anthropic нет JSON-режима: выберите «Автоматически», схему или инструкцию.")
    elif kind == LlmKind.CODEX_CLI:
        if base_url:
            raise ProfileError("У подключения Codex CLI нет адреса API.")
        if params.russia_only:
            raise ProfileError("Codex работает через серверы OpenAI в США: «Только серверы в РФ» недоступна.")
    else:
        raise ProfileError(f"Неизвестный тип подключения: {kind}.")


def list_profiles(db: Session) -> list[LlmProfile]:
    return list(db.scalars(select(LlmProfile).order_by(LlmProfile.name)))


def get_profile(db: Session, name: str) -> LlmProfile:
    profile = db.scalar(select(LlmProfile).where(LlmProfile.name == name.strip()))
    if profile is None:
        raise ProfileError(f"Подключение «{name}» не найдено.")
    return profile


def active_profile(db: Session) -> LlmProfile | None:
    return db.scalar(select(LlmProfile).where(LlmProfile.is_active.is_(True)))


def _store_key(
    settings: Settings, profile: LlmProfile, params: ProfileParams, api_key: str | None
) -> ProfileParams:
    """api_key: None — не менять, "" — удалить, иначе — зашифровать и сохранить."""
    if api_key is None:
        return params
    api_key = api_key.strip()
    if not api_key:
        profile.api_key_enc = None
        return params.model_copy(update={"key_hint": ""})
    if profile.kind == LlmKind.CODEX_CLI:
        raise ProfileError("Codex CLI использует вход в аккаунт ChatGPT, ключ API ему не нужен.")
    profile.api_key_enc = encrypt_secret(settings, api_key)
    return params.model_copy(update={"key_hint": api_key[-4:] if len(api_key) > 8 else ""})


NEW_SERVER_KEY_MESSAGE = (
    "Адрес API указывает на другой сервер: введите ключ API заново. Сохранённый ключ на новый адрес "
    "не отправляется."
)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        return scheme, parts.netloc.lower()
    default = {"http": 80, "https": 443}.get(scheme)
    return scheme, f"{(parts.hostname or '').lower()}:{default if port is None else port}"


def same_server(left: str, right: str) -> bool:
    """Один ли сервер у двух адресов API: схема, имя и порт; путь не важен."""
    return _origin(left) == _origin(right)


def _checked_name(db: Session, name: str, profile_id: int | None = None) -> str:
    name = name.strip()
    if not name or len(name) > 100:
        raise ProfileError("Название подключения — от 1 до 100 символов.")
    existing = db.scalar(select(LlmProfile.id).where(LlmProfile.name == name))
    if existing is not None and existing != profile_id:
        raise ProfileError(f"Подключение «{name}» уже есть.")
    return name


def create_profile(
    db: Session,
    settings: Settings,
    *,
    name: str,
    kind: str,
    base_url: str = "",
    model: str = "",
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
    activate: bool = False,
) -> LlmProfile:
    name = _checked_name(db, name)
    try:
        parsed = ProfileParams.model_validate(params or {})
    except ValidationError as exc:
        raise ProfileError(_validation_message(exc)) from exc
    base_url = base_url.strip().rstrip("/")
    if kind == LlmKind.ANTHROPIC:
        base_url = normalize_anthropic_url(base_url)
    model = model.strip()
    validate_settings(kind, base_url, model, parsed)
    profile = LlmProfile(name=name, kind=kind, base_url=base_url, model=model, is_active=False)
    parsed = _store_key(settings, profile, parsed, api_key)
    profile.params = parsed.model_dump(mode="json")
    db.add(profile)
    db.flush()
    if activate or active_profile(db) is None:
        activate_profile(db, profile)
    return profile


def update_profile(
    db: Session,
    settings: Settings,
    profile: LlmProfile,
    *,
    name: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
    drop_key_on_new_server: bool = False,
) -> LlmProfile:
    """Изменить подключение. Если адрес переехал на другой сервер, а нового ключа нет, сохранённый ключ
    туда не уходит: ошибка для администратора или, при загрузке настроек, ключ сбрасывается."""
    current = profile_params(profile)
    try:
        merged = ProfileParams.model_validate({**current.model_dump(mode="json"), **(params or {})})
    except ValidationError as exc:
        raise ProfileError(_validation_message(exc)) from exc
    new_base_url = profile.base_url if base_url is None else base_url.strip().rstrip("/")
    if profile.kind == LlmKind.ANTHROPIC:
        new_base_url = normalize_anthropic_url(new_base_url)
    new_model = profile.model if model is None else model.strip()
    validate_settings(profile.kind, new_base_url, new_model, merged)
    if (
        api_key is None
        and profile.api_key_enc is not None
        and not same_server(profile.base_url, new_base_url)
    ):
        if not drop_key_on_new_server:
            raise ProfileError(NEW_SERVER_KEY_MESSAGE)
        api_key = ""
    if (
        new_model != profile.model
        or new_base_url != profile.base_url
        or merged.russia_only != current.russia_only
    ):
        # Цены и поддерживаемые параметры относились к прежней модели или провайдеру.
        merged = merged.model_copy(update={"model_info": None})
    if name is not None and name.strip() != profile.name:
        profile.name = _checked_name(db, name, profile.id)
    profile.base_url = new_base_url
    profile.model = new_model
    merged = _store_key(settings, profile, merged, api_key)
    profile.params = merged.model_dump(mode="json")
    return profile


def activate_profile(db: Session, profile: LlmProfile) -> None:
    # Сначала снимаем признак с остальных: уникальный индекс не допускает двух активных профилей.
    db.execute(update(LlmProfile).where(LlmProfile.id != profile.id).values(is_active=False))
    db.flush()
    profile.is_active = True
    db.flush()


def delete_profile(db: Session, profile: LlmProfile) -> None:
    db.delete(profile)
    db.flush()


def build_provider(
    profile: LlmProfile,
    settings: Settings,
    *,
    http_client: httpx2.Client | None = None,
    codex_command: Sequence[str] | None = None,
) -> Provider:
    params = profile_params(profile)
    if profile.kind == LlmKind.OPENAI_COMPAT:
        api_key = decrypt_secret(settings, profile.api_key_enc) if profile.api_key_enc else ""
        return OpenAICompatProvider(
            base_url=profile.base_url,
            api_key=api_key,
            model=profile.model,
            params=params,
            http_client=http_client,
        )
    if profile.kind == LlmKind.ANTHROPIC:
        api_key = decrypt_secret(settings, profile.api_key_enc) if profile.api_key_enc else ""
        return AnthropicProvider(
            base_url=profile.base_url,
            api_key=api_key,
            model=profile.model,
            params=params,
            http_client=http_client,
        )
    if profile.kind == LlmKind.CODEX_CLI:
        return CodexCliProvider(
            command=codex_command or resolve_codex_command(settings.codex_bin),
            home=codex_home(settings),
            model=profile.model,
            params=params,
            temp_root=storage.temp_root(settings),
        )
    raise ProfileError(f"Неизвестный тип подключения: {profile.kind}.")


def check_profile(
    db: Session,
    settings: Settings,
    profile: LlmProfile,
    *,
    http_client: httpx2.Client | None = None,
    codex_command: Sequence[str] | None = None,
) -> CheckReport:
    """Проверка связи. Сведения о модели (цены, параметры) сохраняются в профиле для анализа."""
    provider = build_provider(profile, settings, http_client=http_client, codex_command=codex_command)
    report = provider.check()
    if report.model_info is not None:
        params = profile_params(profile).model_copy(update={"model_info": report.model_info})
        profile.params = params.model_dump(mode="json")
        db.flush()
    return report


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "Неверные параметры подключения: " + "; ".join(parts)
