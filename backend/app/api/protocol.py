"""Протокол разногласий в проверке: черновик, правка, скачивание Word и PDF.

Правит и скачивает протокол владелец проверки; администратор может его посмотреть. Документ собирается
в момент скачивания и не сохраняется. Каждый выпуск записывается в журнал.

Черновик ставит договору без даты сегодняшнюю дату протокола по часам браузера менеджера: интерфейс
передаёт смещение часового пояса (`utc_offset`).
"""

from datetime import date, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, runtime
from app.api.checks import _get_check, _touch
from app.api.deps import AppSettings, CurrentUser, DbSession, UtcOffset
from app.core.db import utcnow
from app.documents.errors import DocumentError
from app.models import Check, Organization, User
from app.organization import get_organization
from app.protocol.builder_docx import build_docx
from app.protocol.editing import (
    ProtocolNotReady,
    ProtocolState,
    draft_from_result,
    file_name,
    preamble_preview,
    to_protocol_data,
)
from app.protocol.model import ProtocolImages
from app.protocol.templates import ProtocolTemplate

router = APIRouter(prefix="/api/checks", tags=["protocol"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class OrganizationStatus(BaseModel):
    ready: bool
    signature: bool
    stamp: bool


class ProtocolOut(BaseModel):
    state: ProtocolState
    # Вступление, которое будет в документе: своё или собранное по шаблону.
    preamble_preview: str
    organization: OrganizationStatus
    # False — черновик из результата анализа, правок ещё не сохраняли.
    saved: bool


def _require_result(check: Check) -> None:
    if not check.result:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Анализ ещё не готов: протокол собирается из его результата."
        )


def _require_owner(check: Check, user: User) -> None:
    if check.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Протокол правит и выпускает владелец проверки.")


def _today(utc_offset: int) -> date:
    """Сегодня у менеджера, а не по UTC: с 00:00 до 03:00 по Москве в UTC ещё вчера."""
    return utcnow().astimezone(timezone(timedelta(minutes=utc_offset))).date()


def _state(
    check: Check, organization: Organization | None, template: ProtocolTemplate, today: date
) -> tuple[ProtocolState, bool]:
    if check.protocol:
        return ProtocolState.model_validate(check.protocol), True
    state = draft_from_result(check.result or {}, check.blocks or [], organization, today, template)
    return state, False


def _out(
    state: ProtocolState, organization: Organization | None, template: ProtocolTemplate, saved: bool
) -> ProtocolOut:
    return ProtocolOut(
        state=state,
        preamble_preview=preamble_preview(state, organization, template),
        organization=OrganizationStatus(
            ready=organization is not None and organization.ready_for_protocol,
            signature=organization is not None and organization.signature_png is not None,
            stamp=organization is not None and organization.stamp_png is not None,
        ),
        saved=saved,
    )


def _load(db: Session, user: User, check_id: str) -> tuple[Check, Organization | None, ProtocolTemplate]:
    check = _get_check(db, user, check_id)
    _require_result(check)
    return check, get_organization(db), runtime.get_template(db)


@router.get("/{check_id}/protocol", response_model=ProtocolOut)
def get_protocol(check_id: str, user: CurrentUser, db: DbSession, utc_offset: UtcOffset = 0) -> ProtocolOut:
    check, organization, template = _load(db, user, check_id)
    state, saved = _state(check, organization, template, _today(utc_offset))
    return _out(state, organization, template, saved)


@router.put("/{check_id}/protocol", response_model=ProtocolOut)
def save_protocol(
    check_id: str, payload: ProtocolState, user: CurrentUser, db: DbSession, settings: AppSettings
) -> ProtocolOut:
    check, organization, template = _load(db, user, check_id)
    _require_owner(check, user)
    check.protocol = payload.model_dump(mode="json")
    _touch(check, user, db, settings)
    db.commit()
    return _out(payload, organization, template, True)


@router.post("/{check_id}/protocol/reset", response_model=ProtocolOut)
def reset_protocol(
    check_id: str, user: CurrentUser, db: DbSession, settings: AppSettings, utc_offset: UtcOffset = 0
) -> ProtocolOut:
    """Отбросить правки и собрать черновик из результата анализа заново."""
    check, organization, template = _load(db, user, check_id)
    _require_owner(check, user)
    check.protocol = None
    _touch(check, user, db, settings)
    db.commit()
    state, saved = _state(check, organization, template, _today(utc_offset))
    return _out(state, organization, template, saved)


def _document(
    db: Session,
    user: User,
    settings: Any,
    check_id: str,
    signature: bool,
    stamp: bool,
    pdf: bool,
    today: date,
) -> Response:
    check, organization, template = _load(db, user, check_id)
    _require_owner(check, user)
    # Без сохранённых правок документ собирается из черновика — с той же датой, что на вкладке «Протокол».
    state, _ = _state(check, organization, template, today)
    try:
        data = to_protocol_data(state, organization, template)
    except ProtocolNotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if organization is None:  # pragma: no cover — to_protocol_data уже отказал бы
        raise HTTPException(status.HTTP_409_CONFLICT, "Не заполнены данные организации.")
    if signature and organization.signature_png is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "В данных организации нет подписи. Обратитесь к администратору."
        )
    if stamp and organization.stamp_png is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "В данных организации нет печати. Обратитесь к администратору."
        )
    images = ProtocolImages(
        header=organization.header_png, signature=organization.signature_png, stamp=organization.stamp_png
    )
    content = build_docx(data, images, with_signature=signature, with_stamp=stamp)
    media_type, extension = DOCX_MIME, "docx"
    if pdf:
        from app.protocol.pdf import docx_to_pdf

        try:
            content = docx_to_pdf(content)
        except DocumentError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF не собран: {exc}") from exc
        media_type, extension = "application/pdf", "pdf"
    audit.record(
        db,
        f"protocol_{extension}",
        user_id=user.id,
        check=check,
        with_signature=signature,
        with_stamp=stamp,
    )
    _touch(check, user, db, settings)
    db.commit()
    name = file_name(state, extension)
    disposition = f"attachment; filename=\"protocol.{extension}\"; filename*=UTF-8''{quote(name)}"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": disposition})


@router.get("/{check_id}/protocol.docx")
def download_docx(
    check_id: str,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    signature: Annotated[bool, Query()] = False,
    stamp: Annotated[bool, Query()] = False,
    utc_offset: UtcOffset = 0,
) -> Response:
    return _document(db, user, settings, check_id, signature, stamp, pdf=False, today=_today(utc_offset))


@router.get("/{check_id}/protocol.pdf")
def download_pdf(
    check_id: str,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    signature: Annotated[bool, Query()] = False,
    stamp: Annotated[bool, Query()] = False,
    utc_offset: UtcOffset = 0,
) -> Response:
    return _document(db, user, settings, check_id, signature, stamp, pdf=True, today=_today(utc_offset))
