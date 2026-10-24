"""Перенос настроек одним файлом (ARCHITECTURE.md, раздел 7.2).

В файл входят версии промпта, подключения ИИ без ключей, данные организации с картинками, шаблон протокола
и лимиты. Пользователей, ключей API, проверок и журнала в нём нет. Загрузка применяет файл целиком
в одной транзакции: версии промпта и подключения добавляются, остальное заменяется.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, runtime
from app.analysis import profiles, prompts
from app.analysis.prompt import prompt_fingerprint
from app.analysis.providers.base import ProfileParams
from app.api.deps import AdminUser, AppSettings, DbSession
from app.core.config import Settings
from app.core.db import utcnow
from app.models import LlmKind, LlmProfile, PromptVersion
from app.organization import (
    ASSETS,
    OrganizationError,
    ensure_organization,
    get_organization,
    load_image,
    validate_requisites,
)
from app.protocol.templates import ProtocolTemplate

router = APIRouter()

FORMAT = "contract-review-settings"
VERSION = 1
MAX_FILE_BYTES = 40 * 1024 * 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
ORGANIZATION_FIELDS = (
    "full_name",
    "short_name",
    "signer",
    "representative",
    "basis",
    "default_city",
    "footer_text",
)


class PromptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=prompts.MAX_PROMPT_LENGTH)
    comment: str = Field(default="", max_length=500)
    is_active: bool = False
    created_at: datetime | None = None


class ProfileItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: LlmKind
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = False


class OrganizationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(default="", max_length=500)
    short_name: str = Field(default="", max_length=300)
    signer: str = Field(default="", max_length=200)
    representative: str = Field(default="", max_length=300)
    basis: str = Field(default="", max_length=300)
    default_city: str = Field(default="", max_length=100)
    footer_text: str = Field(default="", max_length=500)
    requisites: dict[str, str] = Field(default_factory=dict)
    # Картинки в base64; null — картинки нет.
    images: dict[Literal["signature", "stamp", "header"], str | None] = Field(default_factory=dict)


class SettingsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["contract-review-settings"]
    version: int = Field(ge=1, le=VERSION)
    exported_at: datetime | None = None
    prompt_versions: list[PromptItem] = Field(default_factory=list, max_length=1000)
    llm_profiles: list[ProfileItem] = Field(default_factory=list, max_length=100)
    organization: OrganizationItem | None = None
    protocol_template: ProtocolTemplate | None = None
    limits: runtime.Limits | None = None


class ImportReport(BaseModel):
    prompt_versions_added: int
    prompt_active: int | None
    profiles_added: list[str]
    profiles_updated: list[str]
    # Подключения, которым нужно ввести ключ: ключи в файл не входят.
    profiles_need_key: list[str]
    profile_active: str | None
    organization: bool
    protocol_template: bool
    limits: bool
    notes: list[str]


def build_export(db: Session, settings: Settings) -> dict[str, Any]:
    organization = get_organization(db)
    data = SettingsFile(
        format=FORMAT,
        version=VERSION,
        exported_at=utcnow(),
        prompt_versions=[
            PromptItem(
                text=version.text,
                comment=version.comment,
                is_active=version.is_active,
                created_at=version.created_at,
            )
            for version in sorted(prompts.list_versions(db), key=lambda version: version.id)
        ],
        llm_profiles=[
            ProfileItem(
                name=profile.name,
                kind=LlmKind(profile.kind),
                base_url=profile.base_url,
                model=profile.model,
                params=profiles.profile_params(profile).model_dump(
                    mode="json", exclude={"key_hint", "model_info"}
                ),
                is_active=profile.is_active,
            )
            for profile in profiles.list_profiles(db)
        ],
        organization=None
        if organization is None
        else OrganizationItem(
            **{name: getattr(organization, name) or "" for name in ORGANIZATION_FIELDS},
            requisites={key: str(value) for key, value in (organization.requisites or {}).items()},
            images={
                kind: base64.b64encode(getattr(organization, column)).decode("ascii")
                if getattr(organization, column) is not None
                else None
                for kind, column in ASSETS.items()
            },
        ),
        protocol_template=runtime.get_template(db),
        limits=runtime.get_limits(db, settings),
    )
    return data.model_dump(mode="json")


@router.get("/export")
def export_settings(db: DbSession, admin: AdminUser, settings: AppSettings) -> Response:
    content = json.dumps(build_export(db, settings), ensure_ascii=False, indent=2).encode("utf-8")
    audit.record(db, "settings_export", user_id=admin.id)
    db.commit()
    name = quote(f"Настройки contract-review {utcnow().strftime('%Y-%m-%d')}.json")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"contract-review-settings.json\"; filename*=UTF-8''{name}"
            )
        },
    )


def _needs_key(profile: LlmProfile) -> bool:
    """Ключ не нужен Codex и локальным серверам моделей (Ollama, vLLM на этой машине)."""
    if profile.kind == LlmKind.CODEX_CLI:
        return False
    host = urlsplit(profile.base_url).hostname or ""
    return host not in LOCAL_HOSTS


def _decode_images(item: OrganizationItem) -> dict[str, bytes | None]:
    images: dict[str, bytes | None] = {}
    for kind, encoded in item.images.items():
        if encoded is None:
            images[kind] = None
            continue
        try:
            images[kind] = load_image(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            message = str(exc) if isinstance(exc, OrganizationError) else "картинка повреждена"
            raise OrganizationError(f"Организация, {kind}: {message}") from exc
    return images


def apply_import(db: Session, settings: Settings, data: SettingsFile, admin_id: int) -> ImportReport:
    """Применить файл настроек. Ошибка в любой части отменяет всё (вызывающий откатывает транзакцию)."""
    report = ImportReport(
        prompt_versions_added=0,
        prompt_active=None,
        profiles_added=[],
        profiles_updated=[],
        profiles_need_key=[],
        profile_active=None,
        organization=False,
        protocol_template=False,
        limits=False,
        notes=[],
    )
    # Проверки до изменений: картинки и реквизиты.
    images = _decode_images(data.organization) if data.organization else {}
    requisites = validate_requisites(data.organization.requisites) if data.organization else {}

    # Версии промпта: добавляются те, которых нет; активной становится отмеченная в файле.
    existing = {(version.fingerprint, version.text): version for version in db.scalars(select(PromptVersion))}
    active_item: PromptVersion | None = None
    for item in data.prompt_versions:
        text = item.text.strip()
        key = (prompt_fingerprint(text), text)
        version = existing.get(key)
        if version is None:
            version = PromptVersion(
                text=text,
                comment=item.comment.strip()[:500],
                author_id=admin_id,
                fingerprint=key[0],
                is_active=False,
                created_at=item.created_at or utcnow(),
            )
            db.add(version)
            db.flush()
            existing[key] = version
            report.prompt_versions_added += 1
        if item.is_active:
            active_item = version
    if active_item is not None and not active_item.is_active:
        prompts.activate_version(db, active_item)
    if active_item is not None:
        report.prompt_active = active_item.id

    # Подключения: по названию; ключ существующего подключения сохраняется.
    active_before = profiles.active_profile(db)
    file_active: LlmProfile | None = None
    for item in data.llm_profiles:
        params = {key: value for key, value in item.params.items() if key not in ("key_hint", "model_info")}
        try:
            ProfileParams.model_validate(params)
        except ValidationError as exc:
            raise profiles.ProfileError(f"Подключение «{item.name}»: неверные параметры.") from exc
        profile = db.scalar(select(LlmProfile).where(LlmProfile.name == item.name.strip()))
        if profile is None:
            profile = profiles.create_profile(
                db,
                settings,
                name=item.name,
                kind=item.kind,
                base_url=item.base_url,
                model=item.model,
                params=params,
            )
            report.profiles_added.append(profile.name)
        else:
            if profile.kind != item.kind:
                raise profiles.ProfileError(
                    f"Подключение «{profile.name}» здесь другого типа ({profile.kind}): "
                    "переименуйте одно из них."
                )
            had_key = profile.api_key_enc is not None
            # Файл сменил адрес на другой сервер — сохранённый ключ туда не уходит.
            profiles.update_profile(
                db,
                settings,
                profile,
                base_url=item.base_url,
                model=item.model,
                params=params,
                drop_key_on_new_server=True,
            )
            if had_key and profile.api_key_enc is None:
                report.notes.append(
                    f"Подключение «{profile.name}»: адрес API указывает на другой сервер, ключ сброшен. "
                    "Введите ключ заново."
                )
            report.profiles_updated.append(profile.name)
        if profile.api_key_enc is None and _needs_key(profile):
            report.profiles_need_key.append(profile.name)
        if item.is_active:
            file_active = profile
    if file_active is not None:
        # Рабочее подключение не меняется на то, которому ещё нужен ключ. На новой установке
        # активным становится подключение из файла: администратор сразу вводит ему ключ.
        keep_current = active_before is not None and active_before.id != file_active.id
        if file_active.name in report.profiles_need_key and keep_current:
            report.notes.append(
                f"Подключение «{file_active.name}» осталось неактивным: у него нет ключа. "
                "Введите ключ и выберите его."
            )
        else:
            profiles.activate_profile(db, file_active)
    active = profiles.active_profile(db)
    report.profile_active = active.name if active else None

    if data.organization is not None:
        organization = ensure_organization(db)
        for name in ORGANIZATION_FIELDS:
            setattr(organization, name, getattr(data.organization, name).strip())
        organization.requisites = requisites
        for kind, column in ASSETS.items():
            setattr(organization, column, images.get(kind))
        report.organization = True
    if data.protocol_template is not None:
        runtime.set_template(db, data.protocol_template, admin_id)
        report.protocol_template = True
    if data.limits is not None:
        runtime.set_limits(db, data.limits, admin_id)
        report.limits = True
    db.flush()
    return report


@router.post("/import", response_model=ImportReport)
def import_settings(
    db: DbSession,
    admin: AdminUser,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="Файл настроек .json")],
) -> ImportReport:
    raw = file.file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл настроек больше 40 МБ.")
    try:
        data = SettingsFile.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Это не файл настроек contract-review или он повреждён ({location}: {first['msg']}).",
        ) from exc
    try:
        report = apply_import(db, settings, data, admin.id)
    except (OrganizationError, profiles.ProfileError, prompts.PromptError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Настройки не загружены: {exc}") from exc
    audit.record(
        db,
        "settings_import",
        user_id=admin.id,
        details=f"версий промпта добавлено: {report.prompt_versions_added}, подключений: "
        f"{len(report.profiles_added)} новых, {len(report.profiles_updated)} обновлено",
    )
    db.commit()
    return report
