"""Шаблон протокола и настройки: срок жизни проверки, лимит файла.

Предпросмотр собирается на вымышленном договоре с данными нашей организации, подписью и печатью — так
видно, как шаблон и картинки лягут в протокол, без настоящей проверки.
"""

from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app import audit, runtime
from app.api.deps import AdminUser, AppSettings, DbSession
from app.documents.errors import DocumentError
from app.models import Organization
from app.organization import get_organization
from app.protocol.builder_docx import build_docx
from app.protocol.model import Letterhead, ProtocolData, ProtocolImages, ProtocolRow
from app.protocol.templates import (
    APPENDICES_FIELDS,
    CLOSING_FIELDS,
    PREAMBLE_FIELDS,
    ContractParty,
    ProtocolDraft,
    ProtocolTemplate,
    build_protocol_data,
)

router = APIRouter()

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SAMPLE_BUYER = ContractParty(
    full_name="Общество с ограниченной ответственностью «Образец»",
    short_name="ООО «Образец»",
    representative="генерального директора Примерова Андрея Борисовича",
    basis="Устава",
    signer="А.Б. Примеров",
)
SAMPLE_ROWS = [
    ProtocolRow(
        clause="п. 5.2",
        buyer_text="5.2. Оплата производится в течение 60 (шестидесяти) рабочих дней с даты поставки товара.",
        supplier_text="5.2. Оплата производится в течение 14 (четырнадцати) календарных дней с даты поставки "
        "товара.",
    ),
    ProtocolRow(
        clause="п. 9.2",
        buyer_text="9.2. За просрочку поставки Поставщик уплачивает неустойку 0,5 % от стоимости товара за "
        "каждый день просрочки.",
        supplier_text="9.2. За просрочку поставки Поставщик уплачивает неустойку 0,1 % от стоимости "
        "непоставленного в срок товара за каждый день просрочки, но не более 10 % его стоимости.",
    ),
]


class TemplateOut(BaseModel):
    template: ProtocolTemplate
    defaults: ProtocolTemplate
    # Подстановки и что они значат — подсказка в редакторе.
    fields: dict[str, dict[str, str]]


class PreviewOut(BaseModel):
    title: str
    subtitle: str
    preamble: str
    closing_title: str
    closing_items: list[str]


class LimitsOut(BaseModel):
    check_ttl_hours: int
    max_upload_mb: int
    # Значения из .env: действуют, пока администратор не задал свои.
    defaults: dict[str, int]


def _sample(template: ProtocolTemplate, organization: Organization | None) -> ProtocolData:
    supplier = ContractParty(
        full_name=(organization.full_name if organization else "") or "Наименование из раздела «Организация»",
        short_name=organization.short_name if organization else "",
        representative=organization.representative if organization else "",
        basis=organization.basis if organization else "",
        signer=organization.signer if organization else "",
    )
    today = date.today()
    draft = ProtocolDraft(
        contract_kind="поставки",
        contract_number="1/2026",
        contract_date=today,
        city=(organization.default_city if organization else "") or "г. Москва",
        buyer=SAMPLE_BUYER,
        supplier=supplier,
        rows=[row.model_copy() for row in SAMPLE_ROWS],
        protocol_date=today,
        include_agreed_column=template.include_agreed_column,
        appendices=["Спецификация № 1"],
        letterhead=Letterhead(
            enabled=template.letterhead, footer=(organization.footer_text if organization else "") or ""
        ),
        title=template.title,
    )
    data = build_protocol_data(draft, template)
    data.table_font_size = template.table_font_size
    return data


@router.get("/protocol-template", response_model=TemplateOut)
def get_template(db: DbSession, _admin: AdminUser) -> TemplateOut:
    return TemplateOut(
        template=runtime.get_template(db),
        defaults=ProtocolTemplate(),
        fields={"preamble": PREAMBLE_FIELDS, "closing": CLOSING_FIELDS, "appendices": APPENDICES_FIELDS},
    )


@router.put("/protocol-template", response_model=ProtocolTemplate)
def put_template(payload: ProtocolTemplate, db: DbSession, admin: AdminUser) -> ProtocolTemplate:
    if payload != runtime.get_template(db):
        runtime.set_template(db, payload, admin.id)
        audit.record(db, "template_update", user_id=admin.id)
        db.commit()
    return payload


@router.post("/protocol-template/preview", response_model=PreviewOut)
def preview_text(payload: ProtocolTemplate, db: DbSession, _admin: AdminUser) -> PreviewOut:
    data = _sample(payload, get_organization(db))
    return PreviewOut(
        title=data.title,
        subtitle=data.subtitle,
        preamble=data.preamble,
        closing_title=data.closing_title,
        closing_items=data.closing_items,
    )


def _document(payload: ProtocolTemplate, db: Any, signature: bool, stamp: bool, pdf: bool) -> Response:
    organization = get_organization(db)
    images = ProtocolImages(
        header=organization.header_png if organization else None,
        signature=organization.signature_png if organization else None,
        stamp=organization.stamp_png if organization else None,
    )
    content = build_docx(
        _sample(payload, organization),
        images,
        with_signature=signature and images.signature is not None,
        with_stamp=stamp and images.stamp is not None,
    )
    media_type, extension = DOCX_MIME, "docx"
    if pdf:
        from app.protocol.pdf import docx_to_pdf

        try:
            content = docx_to_pdf(content)
        except DocumentError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF не собран: {exc}") from exc
        media_type, extension = "application/pdf", "pdf"
    name = quote(f"Образец протокола.{extension}")
    disposition = f"attachment; filename=\"preview.{extension}\"; filename*=UTF-8''{name}"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": disposition})


@router.post("/protocol-template/preview.docx")
def preview_docx(
    payload: ProtocolTemplate, db: DbSession, _admin: AdminUser, signature: bool = True, stamp: bool = True
) -> Response:
    return _document(payload, db, signature, stamp, pdf=False)


@router.post("/protocol-template/preview.pdf")
def preview_pdf(
    payload: ProtocolTemplate, db: DbSession, _admin: AdminUser, signature: bool = True, stamp: bool = True
) -> Response:
    return _document(payload, db, signature, stamp, pdf=True)


def _limits_out(limits: runtime.Limits, settings: Any) -> LimitsOut:
    return LimitsOut(
        check_ttl_hours=limits.check_ttl_hours,
        max_upload_mb=limits.max_upload_mb,
        defaults={"check_ttl_hours": settings.check_ttl_hours, "max_upload_mb": settings.max_upload_mb},
    )


@router.get("/settings", response_model=LimitsOut)
def get_settings(db: DbSession, _admin: AdminUser, settings: AppSettings) -> LimitsOut:
    return _limits_out(runtime.get_limits(db, settings), settings)


@router.put("/settings", response_model=LimitsOut)
def put_settings(
    payload: runtime.Limits, db: DbSession, admin: AdminUser, settings: AppSettings
) -> LimitsOut:
    current = runtime.get_limits(db, settings)
    if payload != current:
        runtime.set_limits(db, payload, admin.id)
        audit.record(
            db,
            "settings_update",
            user_id=admin.id,
            details=f"срок жизни проверки {current.check_ttl_hours} → {payload.check_ttl_hours} ч, "
            f"лимит файла {current.max_upload_mb} → {payload.max_upload_mb} МБ",
        )
        db.commit()
    return _limits_out(payload, settings)
