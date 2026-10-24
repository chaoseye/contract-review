"""Протокол, который правит менеджер (documentation/ARCHITECTURE.md, раздел 6.1).

Черновик собирается из ответа модели: строка таблицы — риск с новой редакцией. В колонку покупателя идёт
полный текст пункта из договора, а не только цитата модели. Менеджер снимает строки, правит тексты,
добавляет свои пункты и меняет порядок; правки хранятся в проверке (checks.protocol) и живут вместе с ней.
"""

import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Organization
from app.protocol.model import Letterhead, ProtocolData, ProtocolRow
from app.protocol.templates import (
    DEFAULT_TITLE,
    NEW_CLAUSE_BUYER_TEXT,
    ContractParty,
    ProtocolDraft,
    ProtocolTemplate,
    build_protocol_data,
    clause_label,
    contract_title,
    party_intro,
    render_preamble,
)

# Полный текст пункта в колонке покупателя — не длиннее этого; иначе берётся цитата модели.
MAX_CLAUSE_TEXT = 6000


class ProtocolMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=DEFAULT_TITLE, max_length=200)
    contract_kind: str = Field(default="поставки", max_length=100)
    contract_number: str = Field(default="", max_length=100)
    contract_date: date | None = None
    city: str = Field(default="", max_length=100)
    protocol_date: date | None = None
    buyer_full_name: str = Field(default="", max_length=500)
    # Наименование в блоке подписей; пусто — полное наименование.
    buyer_short_name: str = Field(default="", max_length=300)
    buyer_representative: str = Field(default="", max_length=300)
    buyer_basis: str = Field(default="", max_length=300)
    # Расшифровка подписи покупателя; пусто — линия.
    buyer_signer: str = Field(default="", max_length=200)
    supplier_first: bool = False
    include_agreed_column: bool = True
    appendices: list[str] = Field(default_factory=list, max_length=20)
    # Своё вступление вместо собранного по шаблону; пусто — по шаблону.
    preamble: str = Field(default="", max_length=5000)
    table_font_size: float = Field(default=10, ge=8, le=12)


def _row_id() -> str:
    return uuid.uuid4().hex[:12]


class EditableRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_row_id, min_length=1, max_length=40)
    # Риск, из которого получилась строка; у своих строк менеджера — None. Ссылка, а не текст: не обрезается.
    risk_id: str | None = Field(default=None, max_length=200)
    include: bool = True
    clause: str = Field(default="", max_length=200)
    buyer_text: str = Field(default="", max_length=20000)
    supplier_text: str = Field(default="", max_length=20000)


class ProtocolState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ProtocolMeta
    rows: list[EditableRow] = Field(default_factory=list, max_length=300)


class ProtocolNotReady(ValueError):
    """Протокол нельзя собрать: не хватает данных. Текст — для менеджера."""


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _max_length(model: type[BaseModel], name: str) -> int | None:
    """Предел длины поля модели (Field(max_length=…))."""
    for item in model.model_fields[name].metadata:
        limit = getattr(item, "max_length", None)
        if isinstance(limit, int):
            return limit
    return None


def _fitted(model: type[BaseModel], values: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """Строки из ответа модели — в пределах полей, иначе черновик не собрать и протокол отвечает 500.

    Длинное значение обрезается и кончается «…». Возвращает значения и имена обрезанных полей.
    """
    fitted = dict(values)
    cut = set()
    for name, value in values.items():
        limit = _max_length(model, name)
        if isinstance(value, str) and limit is not None and len(value) > limit:
            fitted[name] = value[: limit - 1].rstrip() + "…"
            cut.add(name)
    return fitted, cut


def _clause_key(clause: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", clause)
    return tuple(int(number) for number in numbers) if numbers else (10**6,)


def _clause_text(blocks: Iterable[Mapping[str, Any]], clauses: list[str]) -> str | None:
    wanted = [clause.strip().rstrip(".") for clause in clauses if clause.strip()]
    parts: list[str] = []
    for clause in wanted:
        parts += [
            str(block.get("text", "")).strip()
            for block in blocks
            if block.get("clause") == clause and block.get("kind") != "heading"
        ]
    text = "\n".join(part for part in parts if part)
    return text if text and len(text) <= MAX_CLAUSE_TEXT else None


def draft_from_result(
    result: Mapping[str, Any],
    blocks: Iterable[Mapping[str, Any]],
    organization: Organization | None,
    today: date,
    template: ProtocolTemplate | None = None,
) -> ProtocolState:
    """Черновик протокола из результата анализа: данные договора и строки по рискам с новой редакцией."""
    blocks = list(blocks)
    template = template or ProtocolTemplate()
    answer = result.get("answer") or {}
    contract = answer.get("contract") or {}
    buyer = contract.get("buyer") or {}
    contract_date = _parse_date(contract.get("date"))
    city = contract.get("city") or (organization.default_city if organization is not None else "")
    meta_values, _ = _fitted(
        ProtocolMeta,
        {
            "contract_kind": contract.get("kind") or "поставки",
            "contract_number": contract.get("number") or "",
            "city": city or "",
            "buyer_full_name": buyer.get("name") or "",
            "buyer_representative": buyer.get("representative") or "",
            "buyer_basis": buyer.get("basis") or "",
        },
    )
    meta = ProtocolMeta(
        title=template.title,
        include_agreed_column=template.include_agreed_column,
        table_font_size=template.table_font_size,
        contract_date=contract_date,
        # В образце дата протокола совпадает с датой договора; если её нет — сегодня.
        protocol_date=contract_date or today,
        supplier_first=(contract.get("parties_order") or ["buyer"])[0] == "supplier",
        **meta_values,
    )
    risks = [risk for risk in answer.get("risks", []) if isinstance(risk, dict)]
    risks.sort(key=lambda risk: _clause_key((risk.get("clauses") or [""])[0]))
    rows = []
    for risk in risks[: _max_length(ProtocolState, "rows")]:
        wording = (risk.get("proposed_wording") or "").strip()
        clauses = [str(clause) for clause in risk.get("clauses") or []]
        is_new = bool(risk.get("is_new_clause"))
        if is_new:
            buyer_text = NEW_CLAUSE_BUYER_TEXT
        else:
            buyer_text = _clause_text(blocks, clauses) or str(risk.get("quote") or "")
        risk_id = str(risk.get("id"))
        values, cut = _fitted(
            EditableRow,
            {
                "clause": clause_label(clauses, new_clause=is_new),
                "buyer_text": buyer_text,
                "supplier_text": wording,
            },
        )
        rows.append(
            EditableRow(
                # В протокол по умолчанию — риски с готовой редакцией, кроме низкой критичности. Обрезанная
                # редакция не уходит в протокол, пока менеджер её не проверит.
                include=bool(wording) and risk.get("severity") != "low" and "supplier_text" not in cut,
                # Обрезанный id ссылался бы не на тот риск: слишком длинный — строка без ссылки.
                risk_id=risk_id if len(risk_id) <= (_max_length(EditableRow, "risk_id") or 0) else None,
                **values,
            )
        )
    return ProtocolState(meta=meta, rows=rows)


def _supplier(organization: Organization) -> ContractParty:
    return ContractParty(
        full_name=organization.full_name.strip(),
        short_name=organization.short_name.strip(),
        representative=organization.representative.strip(),
        basis=organization.basis.strip(),
        signer=organization.signer.strip(),
    )


def _buyer(meta: ProtocolMeta) -> ContractParty:
    return ContractParty(
        full_name=meta.buyer_full_name.strip(),
        short_name=meta.buyer_short_name.strip(),
        representative=meta.buyer_representative.strip(),
        basis=meta.buyer_basis.strip(),
        signer=meta.buyer_signer.strip(),
    )


def preamble_preview(
    state: ProtocolState, organization: Organization | None, template: ProtocolTemplate | None = None
) -> str:
    """Вступление, которое будет в протоколе: своё или собранное по шаблону."""
    meta = state.meta
    if meta.preamble.strip():
        return meta.preamble.strip()
    buyer = _buyer(meta)
    supplier_name = organization.full_name.strip() if organization is not None else ""
    title = contract_title(meta.contract_kind, meta.contract_number, meta.contract_date)
    return render_preamble(
        template or ProtocolTemplate(),
        party_intro(buyer.full_name or "____", "Покупатель", buyer.representative, buyer.basis),
        party_intro(
            supplier_name or "____",
            "Поставщик",
            organization.representative if organization else "",
            organization.basis if organization else "",
        ),
        title,
        meta.supplier_first,
        meta.contract_kind,
        meta.contract_number,
        meta.contract_date,
    )


def to_protocol_data(
    state: ProtocolState, organization: Organization | None, template: ProtocolTemplate | None = None
) -> ProtocolData:
    if organization is None or not organization.ready_for_protocol:
        raise ProtocolNotReady(
            "Не заполнены данные нашей организации: нужно полное наименование. Обратитесь к администратору."
        )
    meta = state.meta
    if not meta.buyer_full_name.strip():
        raise ProtocolNotReady("Укажите наименование покупателя в данных протокола.")
    rows = [
        ProtocolRow(clause=row.clause, buyer_text=row.buyer_text, supplier_text=row.supplier_text)
        for row in state.rows
        if row.include
    ]
    if not rows:
        raise ProtocolNotReady(
            "В протоколе нет ни одной строки: отметьте пункты «в протокол» или добавьте свой."
        )
    data = build_protocol_data(
        ProtocolDraft(
            contract_kind=meta.contract_kind,
            contract_number=meta.contract_number,
            contract_date=meta.contract_date,
            city=meta.city,
            buyer=_buyer(meta),
            supplier=_supplier(organization),
            rows=rows,
            protocol_date=meta.protocol_date,
            supplier_first=meta.supplier_first,
            include_agreed_column=meta.include_agreed_column,
            appendices=[item for item in meta.appendices if item.strip()],
            title=meta.title.strip() or DEFAULT_TITLE,
            letterhead=Letterhead(
                enabled=bool(template and template.letterhead),
                footer=organization.footer_text or "",
            ),
        ),
        template,
    )
    if meta.preamble.strip():
        data.preamble = meta.preamble.strip()
    data.table_font_size = meta.table_font_size
    return data


def file_name(state: ProtocolState, extension: str) -> str:
    number = re.sub(r'[\\/:*?"<>|]+', "-", state.meta.contract_number).strip(" .-")
    base = f"Протокол разногласий к договору № {number}" if number else "Протокол разногласий"
    return f"{base}.{extension}"
