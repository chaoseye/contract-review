"""Проверки договоров: загрузка, список, состояние, повторный анализ, удаление.

Менеджер видит только свои проверки, администратор — все. Чужая проверка отвечает 404, а не 403:
так нельзя узнать, существует ли она. Действия владельца продлевают срок жизни проверки.
"""

import shutil
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit, runtime, storage
from app.analysis.requisites import check_side
from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.config import Settings
from app.core.db import utcnow
from app.documents.detect import detect_kind
from app.documents.errors import DocumentError
from app.jobs import queue
from app.models import AuditEntry, Check, CheckStatus, JobKind, Procurement, User
from app.organization import get_organization

router = APIRouter(prefix="/api/checks", tags=["checks"])

MAX_COMMENT = 2000
CHUNK = 1024 * 1024
TOUCH_INTERVAL = timedelta(minutes=1)
PROTOCOL_ACTIONS = ("protocol_docx", "protocol_pdf")


class CheckSummary(BaseModel):
    id: str
    status: str
    progress: str | None
    file_name: str
    file_kind: str
    file_size: int
    error: str | None
    created_at: datetime
    expires_at: datetime
    owner_login: str | None = None
    # Чтобы в списке был договор, а не файл: вид в родительном падеже, номер и покупатель —
    # из правок протокола, потом из ответа модели; число рисков и стоп-факторов; последний выпуск протокола.
    contract_kind: str | None = None
    contract_number: str | None = None
    buyer_name: str | None = None
    risk_count: int | None = None
    stop_count: int | None = None
    protocol_downloaded_at: datetime | None = None
    protocol_format: str | None = None


class CheckDetail(CheckSummary):
    comment: str
    procurement: str
    ocr_used: bool
    page_count: int | None
    warnings: list[str]
    blocks: list[dict[str, Any]] | None
    # Ответ модели и результаты его проверки (app/analysis/validate.py).
    result: dict[str, Any] | None
    # Сведения об анализе: подключение, модель, токены, стоимость, причина, если анализа не было.
    analysis: dict[str, Any] | None
    # Наша сторона и сверка реквизитов с карточкой организации; None — документ не прочитан.
    requisites: dict[str, Any] | None = None


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _contract_fields(check: Check) -> dict[str, Any]:
    """Договор и риски для списка проверок. Без результата анализа — пусто."""
    if not check.result:
        return {}
    answer = check.result.get("answer") or {}
    contract = answer.get("contract") or {}
    meta = (check.protocol or {}).get("meta") or {}
    buyer, number = audit.contract_identity(check)
    risks = [risk for risk in answer.get("risks") or [] if isinstance(risk, dict)]
    return {
        "contract_kind": _text(meta.get("contract_kind")) or _text(contract.get("kind")),
        "contract_number": _text(number),
        "buyer_name": _text(buyer),
        "risk_count": len(risks),
        "stop_count": sum(1 for risk in risks if risk.get("stop_factor") is True),
    }


def _analysis_finished(check: Check) -> datetime | None:
    finished = (check.analysis or {}).get("finished_at")
    if not isinstance(finished, str):
        return None
    try:
        moment = datetime.fromisoformat(finished)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _downloads(db: Session, checks: list[Check]) -> dict[str, tuple[datetime, str]]:
    """Последний выпуск протокола у готовых проверок. Выпуск до последнего анализа не считается."""
    ready = {check.id: check for check in checks if check.status == CheckStatus.READY}
    if not ready:
        return {}
    entries = db.execute(
        select(AuditEntry.check_id, AuditEntry.action, AuditEntry.at)
        .where(AuditEntry.check_id.in_(list(ready)), AuditEntry.action.in_(PROTOCOL_ACTIONS))
        .order_by(AuditEntry.at, AuditEntry.id)
    ).all()
    last: dict[str, tuple[datetime, str]] = {}
    for check_id, action, at in entries:
        finished = _analysis_finished(ready[check_id])
        if finished is None or at >= finished:
            last[check_id] = (at, action.removeprefix("protocol_"))
    return last


def _summary(
    check: Check, user: User, downloads: dict[str, tuple[datetime, str]] | None = None
) -> dict[str, Any]:
    downloaded = (downloads or {}).get(check.id)
    return {
        "id": check.id,
        "status": check.status,
        "progress": check.progress,
        "file_name": check.file_name,
        "file_kind": check.file_kind,
        "file_size": check.file_size,
        "error": check.error,
        "created_at": check.created_at,
        "expires_at": check.expires_at,
        "owner_login": check.owner.login if user.is_admin else None,
        **_contract_fields(check),
        "protocol_downloaded_at": downloaded[0] if downloaded else None,
        "protocol_format": downloaded[1] if downloaded else None,
    }


def clean_file_name(raw: str | None) -> str:
    name = unicodedata.normalize("NFC", (raw or "").replace("\\", "/").rsplit("/", 1)[-1])
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return name[:255] or "договор"


def _get_check(db: Session, user: User, check_id: str) -> Check:
    check = db.get(Check, check_id, options=[selectinload(Check.owner)])
    if check is None or (check.owner_id != user.id and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проверка не найдена или уже удалена")
    return check


def _touch(check: Check, user: User, db: Session, settings: Settings) -> None:
    now = utcnow()
    if check.owner_id == user.id and now - check.last_activity_at >= TOUCH_INTERVAL:
        check.last_activity_at = now
        check.expires_at = now + timedelta(hours=runtime.get_limits(db, settings).check_ttl_hours)


def _save_upload(upload: UploadFile, settings: Settings, limits: runtime.Limits) -> tuple[Path, int]:
    """Сохранить загрузку во временный файл с проверкой размера. Возвращает путь и размер."""
    uploads = storage.uploads_root(settings)
    uploads.mkdir(parents=True, exist_ok=True)
    temp_path = uploads / f"{uuid.uuid4().hex}.part"
    size = 0
    try:
        with temp_path.open("wb") as target:
            while chunk := upload.file.read(CHUNK):
                size += len(chunk)
                if size > limits.max_upload_bytes:
                    raise HTTPException(
                        413,
                        f"Файл больше {limits.max_upload_mb} МБ. Загрузите договор меньшего размера.",
                    )
                target.write(chunk)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path, size


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CheckSummary)
def upload_check(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="Договор: docx, doc или PDF")],
    comment: Annotated[str, Form(max_length=MAX_COMMENT)] = "",
    procurement: Annotated[str, Form(description="commercial, 223fz, 44fz или unknown")] = "unknown",
) -> dict[str, Any]:
    if procurement not in {item.value for item in Procurement}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестный режим закупки.")
    limits = runtime.get_limits(db, settings)
    temp_path, size = _save_upload(file, settings, limits)
    try:
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
        try:
            kind = detect_kind(temp_path)
        except DocumentError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

        now = utcnow()
        check = Check(
            owner_id=user.id,
            status=CheckStatus.UPLOADED,
            progress="Ожидает обработки",
            file_name=clean_file_name(file.filename),
            file_kind=kind.value,
            file_size=size,
            comment=comment.strip(),
            procurement=procurement,
            last_activity_at=now,
            created_at=now,
            expires_at=now + timedelta(hours=limits.check_ttl_hours),
        )
        db.add(check)
        db.flush()
        source = storage.source_path(settings, check.id, check.file_kind)
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), source)
        queue.enqueue(db, JobKind.EXTRACT, check.id)
        audit.record(db, "upload", user_id=user.id, check=check)
        try:
            db.commit()
        except Exception:
            storage.remove_check_files(settings, check.id)
            raise
        return _summary(check, user)
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("", response_model=list[CheckSummary])
def list_checks(user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    query = select(Check).options(selectinload(Check.owner)).order_by(Check.created_at.desc())
    if not user.is_admin:
        query = query.where(Check.owner_id == user.id)
    checks = list(db.scalars(query).all())
    downloads = _downloads(db, checks)
    return [_summary(check, user, downloads) for check in checks]


@router.get("/{check_id}", response_model=CheckDetail)
def get_check(check_id: str, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    check = _get_check(db, user, check_id)
    _touch(check, user, db, settings)
    db.commit()
    return {
        **_summary(check, user, _downloads(db, [check])),
        "comment": check.comment,
        "procurement": check.procurement,
        "ocr_used": check.ocr_used,
        "page_count": check.page_count,
        "warnings": check.warnings or [],
        "blocks": check.blocks,
        "result": check.result,
        "analysis": check.analysis,
        # Считается при каждом открытии: правка карточки организации видна сразу.
        "requisites": check_side(check.blocks, get_organization(db)).to_dict() if check.blocks else None,
    }


@router.post("/{check_id}/analyze", status_code=status.HTTP_202_ACCEPTED, response_model=CheckSummary)
def analyze_check(check_id: str, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Запустить анализ заново: другая модель, новый промпт или ошибка провайдера в прошлый раз."""
    from app.jobs.tasks import start_analysis

    check = _get_check(db, user, check_id)
    if check.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Анализ запускает владелец проверки.")
    if not check.blocks or check.status in (CheckStatus.UPLOADED, CheckStatus.EXTRACTING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Документ ещё не прочитан.")
    if queue.has_pending(db, check.id, JobKind.ANALYZE):
        raise HTTPException(status.HTTP_409_CONFLICT, "Анализ уже идёт.")
    if not start_analysis(db, check):
        raise HTTPException(status.HTTP_409_CONFLICT, check.analysis["skipped"])
    _touch(check, user, db, settings)
    db.commit()
    return _summary(check, user)


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_check(check_id: str, user: CurrentUser, db: DbSession, settings: AppSettings) -> None:
    check = _get_check(db, user, check_id)
    audit.record(db, "delete", user_id=user.id, check=check)
    storage.remove_check_files(settings, check.id)
    db.delete(check)
    db.commit()
