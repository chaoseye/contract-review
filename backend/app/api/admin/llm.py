"""Подключения ИИ: профили, активный профиль, проверка связи, модели с ценами, вход в Codex.

Ключ API принимается только в теле запроса и не возвращается: в ответе видны последние 4 символа.
Тесты подменяют HTTP-клиент (общий для openai_compat и anthropic) и команду Codex через app.state
(llm_http_client, codex_command) — без сети.
"""

import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.analysis import presets, profiles
from app.analysis.providers.anthropic_api import AnthropicProvider
from app.analysis.providers.base import ModelInfo, ProfileParams, Provider, ProviderError
from app.analysis.providers.codex_cli import DeviceLogin, codex_logout, codex_status, resolve_codex_command
from app.analysis.providers.openai_compat import OpenAICompatProvider
from app.api.deps import AdminUser, AppSettings, DbSession
from app.core.config import Settings
from app.core.crypto import SecretKeyError, decrypt_secret
from app.core.db import utcnow
from app.models import LlmKind, LlmProfile

router = APIRouter()

# Поля, которые заполняет система: из запроса администратора они не принимаются.
SYSTEM_PARAMS = ("key_hint", "model_info")
CODEX_LOGIN_TIMEOUT = 15 * 60
MAX_MODELS = 500


class PresetOut(BaseModel):
    id: str
    title: str
    base_url: str
    needs_key: bool
    russia_only: bool
    kind: LlmKind
    effort: str | None


class ProfileOut(BaseModel):
    id: int
    name: str
    kind: str
    base_url: str
    model: str
    is_active: bool
    has_key: bool
    key_hint: str
    params: dict[str, Any]
    model_info: ModelInfo | None


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: LlmKind
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    # None — без ключа (Ollama, Codex).
    api_key: str | None = Field(default=None, max_length=1000)
    params: dict[str, Any] = Field(default_factory=dict)
    activate: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    # None — не менять, "" — удалить, иначе — новый ключ.
    api_key: str | None = Field(default=None, max_length=1000)
    params: dict[str, Any] | None = None


class CheckOut(BaseModel):
    ok: bool
    message: str
    detail: str | None = None
    notes: list[str] = Field(default_factory=list)
    model_info: ModelInfo | None = None
    model: str | None = None
    response_format: str | None = None
    duration_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_rub: float | None = None
    cost_estimated: bool = False


class ModelsIn(BaseModel):
    # Тип ещё не сохранённого подключения; у сохранённого (profile_id) берётся его тип.
    kind: LlmKind = LlmKind.OPENAI_COMPAT
    base_url: str = Field(default="", max_length=500)
    # Ключ ещё не сохранённого подключения; если пусто и задан profile_id — ключ этого подключения.
    api_key: str | None = Field(default=None, max_length=1000)
    profile_id: int | None = None
    search: str = Field(default="", max_length=200)


class ModelsOut(BaseModel):
    models: list[ModelInfo]
    total: int


class CodexLoginOut(BaseModel):
    state: str  # waiting, done, failed, cancelled
    url: str | None
    code: str | None
    lines: list[str]
    started_at: datetime


class CodexOut(BaseModel):
    installed: bool
    logged_in: bool
    message: str
    login: CodexLoginOut | None = None


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {key: value for key, value in params.items() if key not in SYSTEM_PARAMS}


def _out(profile: LlmProfile) -> ProfileOut:
    try:
        params = profiles.profile_params(profile)
    except profiles.ProfileError:
        params = ProfileParams()
    data = params.model_dump(mode="json", exclude={"key_hint", "model_info"})
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        kind=profile.kind,
        base_url=profile.base_url,
        model=profile.model,
        is_active=profile.is_active,
        has_key=profile.api_key_enc is not None,
        key_hint=params.key_hint,
        params=data,
        model_info=params.model_info,
    )


def _get(db: Session, profile_id: int) -> LlmProfile:
    profile = db.get(LlmProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Подключение не найдено")
    return profile


def _bad_request(exc: Exception) -> HTTPException:
    message = exc.message if isinstance(exc, ProviderError) else str(exc)
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def codex_command(request: Request, settings: Settings) -> list[str]:
    override = getattr(request.app.state, "codex_command", None)
    return list(override) if override else resolve_codex_command(settings.codex_bin)


def provider_for(request: Request, profile: LlmProfile, settings: Settings) -> Provider:
    return profiles.build_provider(
        profile,
        settings,
        http_client=getattr(request.app.state, "llm_http_client", None),
        codex_command=codex_command(request, settings) if profile.kind == LlmKind.CODEX_CLI else None,
    )


@router.get("/llm-presets", response_model=list[PresetOut])
def list_presets(_admin: AdminUser) -> list[PresetOut]:
    return [
        PresetOut(
            id=key,
            title=preset.title,
            base_url=preset.base_url,
            needs_key=preset.needs_key,
            russia_only=preset.russia_only,
            kind=LlmKind(preset.kind),
            effort=preset.effort,
        )
        for key, preset in presets.PRESETS.items()
    ]


@router.get("/llm-profiles", response_model=list[ProfileOut])
def list_profiles(db: DbSession, _admin: AdminUser) -> list[ProfileOut]:
    return [_out(profile) for profile in profiles.list_profiles(db)]


@router.post("/llm-profiles", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileCreate, db: DbSession, admin: AdminUser, settings: AppSettings
) -> ProfileOut:
    try:
        profile = profiles.create_profile(
            db,
            settings,
            name=payload.name,
            kind=payload.kind,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key or None,
            params=_clean_params(payload.params),
            activate=payload.activate,
        )
    except (profiles.ProfileError, SecretKeyError) as exc:
        raise _bad_request(exc) from exc
    audit.record(
        db,
        "llm_create",
        user_id=admin.id,
        model=profile.model or None,
        details=f"«{profile.name}» ({profile.kind}){', активное' if profile.is_active else ''}",
    )
    db.commit()
    return _out(profile)


@router.patch("/llm-profiles/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: int, payload: ProfileUpdate, db: DbSession, admin: AdminUser, settings: AppSettings
) -> ProfileOut:
    profile = _get(db, profile_id)
    try:
        profiles.update_profile(
            db,
            settings,
            profile,
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            params=_clean_params(payload.params),
        )
    except (profiles.ProfileError, SecretKeyError) as exc:
        raise _bad_request(exc) from exc
    key_note = (
        ""
        if payload.api_key is None
        else ("; ключ удалён" if not payload.api_key.strip() else "; новый ключ")
    )
    audit.record(
        db, "llm_update", user_id=admin.id, model=profile.model or None, details=f"«{profile.name}»{key_note}"
    )
    db.commit()
    return _out(profile)


@router.post("/llm-profiles/{profile_id}/activate", response_model=ProfileOut)
def activate_profile(profile_id: int, db: DbSession, admin: AdminUser) -> ProfileOut:
    profile = _get(db, profile_id)
    if not profile.is_active:
        profiles.activate_profile(db, profile)
        audit.record(
            db, "llm_activate", user_id=admin.id, model=profile.model or None, details=f"«{profile.name}»"
        )
        db.commit()
    return _out(profile)


@router.delete("/llm-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: int, db: DbSession, admin: AdminUser) -> None:
    profile = _get(db, profile_id)
    name, was_active = profile.name, profile.is_active
    profiles.delete_profile(db, profile)
    note = " (было активным: новые проверки не анализируются, пока не выбрано другое)" if was_active else ""
    audit.record(db, "llm_delete", user_id=admin.id, details=f"«{name}»{note}")
    db.commit()


@router.post("/llm-profiles/{profile_id}/test", response_model=CheckOut)
def test_profile(
    profile_id: int, request: Request, db: DbSession, admin: AdminUser, settings: AppSettings
) -> CheckOut:
    """Проверка связи коротким запросом без договора. Сведения о модели и цены сохраняются в профиле."""
    profile = _get(db, profile_id)
    try:
        report = profiles.check_profile(
            db,
            settings,
            profile,
            http_client=getattr(request.app.state, "llm_http_client", None),
            codex_command=codex_command(request, settings) if profile.kind == LlmKind.CODEX_CLI else None,
        )
    except (profiles.ProfileError, SecretKeyError) as exc:
        return CheckOut(ok=False, message=str(exc))
    except ProviderError as exc:
        audit.record(
            db, "llm_test", user_id=admin.id, model=profile.model or None, details=f"«{profile.name}»: ошибка"
        )
        db.commit()
        return CheckOut(ok=False, message=exc.message, detail=exc.detail)
    result, usage = report.result, report.result.usage
    audit.record(
        db,
        "llm_test",
        user_id=admin.id,
        model=result.model,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
        cost_rub=usage.cost_rub,
        details=f"«{profile.name}»: связь есть",
    )
    db.commit()
    return CheckOut(
        ok=True,
        message="Связь есть.",
        notes=report.notes,
        model_info=report.model_info,
        model=result.model,
        response_format=result.response_format,
        duration_seconds=round(result.duration_seconds, 1),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_rub=usage.cost_rub,
        cost_estimated=usage.cost_estimated,
    )


@router.post("/llm-models", response_model=ModelsOut)
def list_models(
    payload: ModelsIn, request: Request, db: DbSession, _admin: AdminUser, settings: AppSettings
) -> ModelsOut:
    """Модели провайдера с ценами — и для ещё не сохранённого подключения (адрес и ключ из формы)."""
    kind = payload.kind
    base_url = payload.base_url.strip().rstrip("/")
    api_key = (payload.api_key or "").strip()
    if payload.profile_id is not None:
        profile = _get(db, payload.profile_id)
        kind = LlmKind(profile.kind)
        base_url = base_url or profile.base_url
        if not api_key and profile.api_key_enc and not profiles.same_server(profile.base_url, base_url):
            # Опечатка в адресе отправила бы сохранённый ключ чужому серверу.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, profiles.NEW_SERVER_KEY_MESSAGE)
        if not api_key and profile.api_key_enc:
            try:
                api_key = decrypt_secret(settings, profile.api_key_enc)
            except SecretKeyError as exc:
                raise _bad_request(exc) from exc
    if kind == LlmKind.CODEX_CLI:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "У Codex CLI нет списка моделей.")
    if kind == LlmKind.ANTHROPIC:
        base_url = base_url or presets.PRESETS["anthropic"].base_url
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Адрес API должен начинаться с http:// или https://."
        )
    provider_class = AnthropicProvider if kind == LlmKind.ANTHROPIC else OpenAICompatProvider
    try:
        provider = provider_class(
            base_url=base_url,
            api_key=api_key,
            model="-",
            params=ProfileParams(max_retries=0, timeout_seconds=60),
            http_client=getattr(request.app.state, "llm_http_client", None),
        )
        models = provider.list_models()
    except ProviderError as exc:
        message = exc.message + (f" Ответ провайдера: {exc.detail}" if exc.detail else "")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc
    search = payload.search.strip().lower()
    found = [
        info for info in models if not search or search in info.id.lower() or search in info.name.lower()
    ]
    return ModelsOut(models=found[:MAX_MODELS], total=len(found))


# --- Codex -------------------------------------------------------------------------------


class CodexLogin:
    """Вход по коду устройства в фоне: администратор открывает ссылку и вводит код, страница ждёт входа."""

    def __init__(self, login: DeviceLogin) -> None:
        self.login = login
        self.state = "waiting"
        self.started_at = utcnow()
        self._thread = threading.Thread(target=self._wait, name="codex-login-wait", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _wait(self) -> None:
        ok = self.login.wait(CODEX_LOGIN_TIMEOUT)
        if self.state == "waiting":
            self.state = "done" if ok else "failed"

    def cancel(self) -> None:
        self.state = "cancelled"
        self.login.cancel()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def out(self) -> CodexLoginOut:
        return CodexLoginOut(
            state=self.state,
            url=self.login.url,
            code=self.login.code,
            lines=list(self.login.lines[-20:]),
            started_at=self.started_at,
        )


def _codex_state(request: Request, settings: Settings) -> CodexOut:
    current: CodexLogin | None = getattr(request.app.state, "codex_login", None)
    try:
        result = codex_status(codex_command(request, settings), profiles.codex_home(settings))
    except ProviderError as exc:
        return CodexOut(
            installed=False, logged_in=False, message=exc.message, login=current.out() if current else None
        )
    return CodexOut(
        installed=True,
        logged_in=result.logged_in,
        message=result.message,
        login=current.out() if current else None,
    )


@router.get("/codex", response_model=CodexOut)
def codex_info(request: Request, _admin: AdminUser, settings: AppSettings) -> CodexOut:
    return _codex_state(request, settings)


@router.post("/codex/login", response_model=CodexOut)
def codex_login(request: Request, db: DbSession, admin: AdminUser, settings: AppSettings) -> CodexOut:
    """Начать вход: Codex показывает ссылку и код. Повторный запрос, пока вход идёт, возвращает тот же код."""
    current: CodexLogin | None = getattr(request.app.state, "codex_login", None)
    if current is None or current.state != "waiting":
        login = DeviceLogin(codex_command(request, settings), profiles.codex_home(settings))
        try:
            login.start()
        except ProviderError as exc:
            raise _bad_request(exc) from exc
        login.wait_for_code(30)
        current = CodexLogin(login)
        current.start()
        request.app.state.codex_login = current
        audit.record(db, "codex_login", user_id=admin.id, details="начат вход по коду устройства")
        db.commit()
    return _codex_state(request, settings)


@router.post("/codex/login/cancel", response_model=CodexOut)
def codex_login_cancel(request: Request, _admin: AdminUser, settings: AppSettings) -> CodexOut:
    current: CodexLogin | None = getattr(request.app.state, "codex_login", None)
    if current is not None and current.state == "waiting":
        current.cancel()
    return _codex_state(request, settings)


@router.post("/codex/logout", response_model=CodexOut)
def codex_logout_route(request: Request, db: DbSession, admin: AdminUser, settings: AppSettings) -> CodexOut:
    try:
        codex_logout(codex_command(request, settings), profiles.codex_home(settings))
    except ProviderError as exc:
        raise _bad_request(exc) from exc
    request.app.state.codex_login = None
    audit.record(db, "codex_logout", user_id=admin.id)
    db.commit()
    return _codex_state(request, settings)
