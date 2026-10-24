"""Наша организация: наименование и подписант для протокола, реквизиты для сверки, подпись, печать, шапка.

Организация одна, поэтому путь без идентификатора. Подпись и печать показываются в предпросмотре
так, как лягут в протокол: с прозрачным фоном, без пустых полей, печать — повёрнутой и полупрозрачной.
Если фон и после этого не прозрачный, администратор видит предупреждение.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from app import audit
from app.api.deps import AdminUser, DbSession
from app.models import Organization
from app.organization import (
    ASSET_LABELS,
    ASSETS,
    MAX_IMAGE_BYTES,
    OrganizationError,
    ensure_organization,
    get_organization,
    set_asset,
    validate_requisites,
)
from app.protocol.assets import covers_text, prepare_signature, prepare_stamp

router = APIRouter(prefix="/organization")

AssetKind = Literal["signature", "stamp", "header"]
# Картинки поверх текста протокола: их фон должен быть прозрачным.
OVERLAY_ASSETS = ("signature", "stamp")
TEXT_FIELDS = ("full_name", "short_name", "signer", "representative", "basis", "default_city", "footer_text")
FIELD_LABELS = {
    "full_name": "полное наименование",
    "short_name": "краткое наименование",
    "signer": "подписант",
    "representative": "представитель",
    "basis": "основание",
    "default_city": "город",
    "footer_text": "контакты для колонтитула",
}


class OrganizationOut(BaseModel):
    full_name: str
    short_name: str
    signer: str
    representative: str
    basis: str
    default_city: str
    footer_text: str
    requisites: dict[str, str]
    assets: dict[str, bool]
    # Подпись и печать, чей фон и после подготовки закроет линию подписи и «М.П.».
    opaque_background: dict[str, bool]
    ready_for_protocol: bool
    updated_at: datetime | None


class OrganizationIn(BaseModel):
    full_name: str = Field(default="", max_length=500)
    short_name: str = Field(default="", max_length=300)
    signer: str = Field(default="", max_length=200)
    representative: str = Field(default="", max_length=300)
    basis: str = Field(default="", max_length=300)
    default_city: str = Field(default="", max_length=100)
    footer_text: str = Field(default="", max_length=500)
    requisites: dict[str, str] = Field(default_factory=dict)


def _opaque(data: bytes | None) -> bool:
    if data is None:
        return False
    try:
        return covers_text(data)
    except OSError:
        # Картинку не прочитать целиком: страница «Организация» всё равно открывается, чтобы её заменить.
        return False


def _out(organization: Organization | None) -> OrganizationOut:
    if organization is None:
        return OrganizationOut(
            **dict.fromkeys(TEXT_FIELDS, ""),
            requisites={},
            assets=dict.fromkeys(ASSETS, False),
            opaque_background=dict.fromkeys(OVERLAY_ASSETS, False),
            ready_for_protocol=False,
            updated_at=None,
        )
    return OrganizationOut(
        **{name: getattr(organization, name) or "" for name in TEXT_FIELDS},
        requisites={key: str(value) for key, value in (organization.requisites or {}).items()},
        assets={kind: getattr(organization, column) is not None for kind, column in ASSETS.items()},
        opaque_background={kind: _opaque(getattr(organization, ASSETS[kind])) for kind in OVERLAY_ASSETS},
        ready_for_protocol=organization.ready_for_protocol,
        updated_at=organization.updated_at,
    )


@router.get("", response_model=OrganizationOut)
def get_data(db: DbSession, _admin: AdminUser) -> OrganizationOut:
    return _out(get_organization(db))


@router.put("", response_model=OrganizationOut)
def put_data(payload: OrganizationIn, db: DbSession, admin: AdminUser) -> OrganizationOut:
    try:
        requisites = validate_requisites(payload.requisites)
    except OrganizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    organization = ensure_organization(db)
    changed = [
        FIELD_LABELS[name]
        for name in TEXT_FIELDS
        if (getattr(organization, name) or "") != getattr(payload, name).strip()
    ]
    for name in TEXT_FIELDS:
        setattr(organization, name, getattr(payload, name).strip())
    if requisites != (organization.requisites or {}):
        changed.append("реквизиты")
    organization.requisites = requisites
    if changed:
        audit.record(db, "org_update", user_id=admin.id, details=", ".join(changed))
    db.commit()
    return _out(organization)


def _image_type(data: bytes) -> str:
    return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"


@router.get("/assets/{kind}")
def get_asset(kind: AssetKind, db: DbSession, _admin: AdminUser, prepared: bool = False) -> Response:
    """Картинка как загружена или (prepared=true) как ляжет в протокол."""
    organization = get_organization(db)
    data = getattr(organization, ASSETS[kind]) if organization is not None else None
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Картинка «{ASSET_LABELS[kind]}» не загружена")
    if prepared and kind == "signature":
        data = prepare_signature(data)[0]
    elif prepared and kind == "stamp":
        data = prepare_stamp(data, "предпросмотр")[0]
    return Response(content=data, media_type=_image_type(data), headers={"Cache-Control": "no-store"})


@router.put("/assets/{kind}", response_model=OrganizationOut)
def put_asset(
    kind: AssetKind,
    db: DbSession,
    admin: AdminUser,
    file: Annotated[UploadFile, File(description="PNG или JPEG до 5 МБ")],
) -> OrganizationOut:
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    organization = ensure_organization(db)
    try:
        set_asset(organization, kind, data)
    except OrganizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit.record(db, "org_asset", user_id=admin.id, details=f"{ASSET_LABELS[kind]}: загружена")
    db.commit()
    return _out(organization)


@router.delete("/assets/{kind}", response_model=OrganizationOut)
def delete_asset(kind: AssetKind, db: DbSession, admin: AdminUser) -> OrganizationOut:
    organization = ensure_organization(db)
    if getattr(organization, ASSETS[kind]) is not None:
        set_asset(organization, kind, None)
        audit.record(db, "org_asset", user_id=admin.id, details=f"{ASSET_LABELS[kind]}: удалена")
        db.commit()
    return _out(organization)
