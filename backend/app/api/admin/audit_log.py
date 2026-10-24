"""Журнал действий: фильтры по времени, пользователю, действию и проверке, итоги, выгрузка CSV.

Время в CSV — по часовому поясу браузера администратора: интерфейс передаёт смещение от UTC в минутах.
"""

import codecs
import csv
import io
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import AdminUser, DbSession, UtcOffset
from app.models import AuditEntry, User

router = APIRouter(prefix="/audit")

MAX_LIMIT = 500
MAX_CSV_ROWS = 100_000


class AuditItem(BaseModel):
    id: int
    at: datetime
    user_id: int | None
    login: str | None
    action: str
    action_label: str
    check_id: str | None
    counterparty: str | None
    contract_number: str | None
    with_signature: bool | None
    with_stamp: bool | None
    model: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_rub: float | None
    prompt_version: str | None
    details: str | None


class AuditTotals(BaseModel):
    count: int
    tokens_in: int
    tokens_out: int
    cost_rub: float


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int
    totals: AuditTotals
    actions: dict[str, str]


class Filters:
    def __init__(
        self,
        at_from: Annotated[
            datetime | None, Query(description="с какого момента, ISO 8601 с часовым поясом")
        ] = None,
        at_to: Annotated[datetime | None, Query(description="до какого момента (не включая)")] = None,
        user_id: int | None = None,
        action: Annotated[list[str] | None, Query()] = None,
        check_id: str | None = None,
    ) -> None:
        self.at_from = _aware(at_from)
        self.at_to = _aware(at_to)
        self.user_id = user_id
        self.action = [item for item in action or [] if item]
        self.check_id = (check_id or "").strip() or None

    def apply(self, query: Select[Any]) -> Select[Any]:
        if self.at_from is not None:
            query = query.where(AuditEntry.at >= self.at_from)
        if self.at_to is not None:
            query = query.where(AuditEntry.at < self.at_to)
        if self.user_id is not None:
            query = query.where(AuditEntry.user_id == self.user_id)
        if self.action:
            query = query.where(AuditEntry.action.in_(self.action))
        if self.check_id:
            query = query.where(AuditEntry.check_id == self.check_id)
        return query


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _items(db: Session, entries: list[AuditEntry]) -> list[AuditItem]:
    ids = {entry.user_id for entry in entries if entry.user_id is not None}
    logins = dict(db.execute(select(User.id, User.login).where(User.id.in_(ids))).all()) if ids else {}
    return [
        AuditItem(
            id=entry.id,
            at=entry.at,
            user_id=entry.user_id,
            login=logins.get(entry.user_id) if entry.user_id is not None else None,
            action=entry.action,
            action_label=audit.ACTION_LABELS.get(entry.action, entry.action),
            check_id=entry.check_id,
            counterparty=entry.counterparty,
            contract_number=entry.contract_number,
            with_signature=entry.with_signature,
            with_stamp=entry.with_stamp,
            model=entry.model,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
            cost_rub=entry.cost_rub,
            prompt_version=entry.prompt_version,
            details=entry.details,
        )
        for entry in entries
    ]


@router.get("", response_model=AuditPage)
def list_entries(
    db: DbSession,
    _admin: AdminUser,
    filters: Annotated[Filters, Depends()],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    totals = db.execute(
        filters.apply(
            select(
                func.count(AuditEntry.id),
                func.coalesce(func.sum(AuditEntry.tokens_in), 0),
                func.coalesce(func.sum(AuditEntry.tokens_out), 0),
                func.coalesce(func.sum(AuditEntry.cost_rub), 0.0),
            )
        )
    ).one()
    entries = list(
        db.scalars(
            filters.apply(select(AuditEntry))
            .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return AuditPage(
        items=_items(db, entries),
        total=totals[0],
        totals=AuditTotals(
            count=totals[0],
            tokens_in=int(totals[1]),
            tokens_out=int(totals[2]),
            cost_rub=round(float(totals[3]), 4),
        ),
        actions=audit.ACTION_LABELS,
    )


def _cell(value: str | None) -> str:
    """Текст из договора не должен стать формулой в Excel: «=», «+», «-», «@» в начале экранируются."""
    text = value or ""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("да" if value else "нет")


@router.get(".csv")
def export_csv(
    db: DbSession,
    _admin: AdminUser,
    filters: Annotated[Filters, Depends()],
    utc_offset: UtcOffset = 0,
) -> Response:
    """CSV для Excel: UTF-8 с BOM, разделитель «;»."""
    zone = timezone(timedelta(minutes=utc_offset))
    entries = list(
        db.scalars(
            filters.apply(select(AuditEntry)).order_by(AuditEntry.at, AuditEntry.id).limit(MAX_CSV_ROWS)
        )
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Дата и время",
            "Пользователь",
            "Действие",
            "Проверка",
            "Контрагент",
            "Номер договора",
            "Подпись",
            "Печать",
            "Модель",
            "Токены (вход)",
            "Токены (выход)",
            "Стоимость, ₽",
            "Версия промпта",
            "Подробности",
        ]
    )
    for item in _items(db, entries):
        writer.writerow(
            [
                item.at.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"),
                _cell(item.login),
                item.action_label,
                item.check_id or "",
                _cell(item.counterparty),
                _cell(item.contract_number),
                _yes_no(item.with_signature),
                _yes_no(item.with_stamp),
                _cell(item.model),
                "" if item.tokens_in is None else item.tokens_in,
                "" if item.tokens_out is None else item.tokens_out,
                "" if item.cost_rub is None else f"{item.cost_rub:.4f}".replace(".", ","),
                item.prompt_version or "",
                _cell(item.details),
            ]
        )
    content = codecs.BOM_UTF8 + buffer.getvalue().encode("utf-8")
    name = quote(f"Журнал {datetime.now(zone).strftime('%Y-%m-%d')}.csv")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"audit.csv\"; filename*=UTF-8''{name}"},
    )
