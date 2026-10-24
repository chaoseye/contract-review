"""Тексты протокола по умолчанию (documentation/PROTOCOL_FORMAT.md, раздел 7).

Администратор правит шаблон в админке (ProtocolTemplate хранится в таблице settings); здесь — тексты
по умолчанию, проверка подстановок и сборка данных протокола.
"""

import re
import string
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.protocol.model import Letterhead, ProtocolData, ProtocolRow, SignatureParty

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip

DEFAULT_TITLE = "ПРОТОКОЛ РАЗНОГЛАСИЙ"
# Неразрывный пробел: «№ 12/2026», «п. 9.2», «2026 г.» не разрываются переносом строки.
NBSP = "\u00a0"
NEW_CLAUSE_BUYER_TEXT = "Пункт отсутствует."


def long_date(value: date) -> str:
    """31 июля 2026 г."""
    return f"{value.day}{NBSP}{MONTHS_GENITIVE[value.month - 1]} {value.year}{NBSP}г."


def short_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def clause_label(numbers: Sequence[str], new_clause: bool = False) -> str:
    """«п. 9.2», «пп. 6.1, 6.3 и 6.5», «п. 9.9 (новый пункт)»."""
    cleaned = [number.strip().rstrip(".") for number in numbers if number.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        label = f"п.{NBSP}{cleaned[0]}"
    else:
        label = f"пп.{NBSP}" + ", ".join(cleaned[:-1]) + " и " + cleaned[-1]
    return f"{label} (новый пункт)" if new_clause else label


def named_as(full_name: str) -> str:
    """Причастие «именуемое / именуемый / именуемая», согласованное с формой организации."""
    lowered = full_name.strip().lower()
    if lowered.startswith(("индивидуальный предприниматель", "ип ")):
        # Отчество на «-вна»/«-чна» — предприниматель-женщина.
        return "именуемая" if re.search(r"(вна|чна|кызы)\b", lowered) else "именуемый"
    if lowered.startswith(("компания", "корпорация", "фирма", "организация")):
        return "именуемая"
    # Общество, акционерное общество, учреждение, предприятие, товарищество и сокращения ООО/АО/ПАО.
    return "именуемое"


def party_intro(full_name: str, role: str, representative: str = "", basis: str = "") -> str:
    """Представление стороны во вступлении.

    «АО «Пример», именуемое в дальнейшем «Покупатель», в лице …, действующего на основании …».
    """
    text = f"{full_name.strip()}, {named_as(full_name)} в дальнейшем «{role}»"
    if representative.strip():
        text += f", в лице {representative.strip()}"
        if basis.strip():
            text += f", действующего на основании {basis.strip()}"
    return text


def contract_title(kind: str, number: str, contract_date: date | None) -> str:
    """«Договору поставки № 12/2026 от 31.07.2026» (в дательном падеже, для «к …»)."""
    kind_part = f" {kind.strip()}" if kind.strip() else ""
    number_part = f" №{NBSP}{number.strip()}" if number.strip() else f" №{NBSP}____"
    date_part = f" от {short_date(contract_date)}" if contract_date else ""
    return f"Договору{kind_part}{number_part}{date_part}"


DEFAULT_PREAMBLE = (
    "{first_party}, с одной стороны, и {second_party}, с другой стороны, совместно именуемые «Стороны», "
    "составили настоящий Протокол разногласий к {contract} (далее — Договор) о нижеследующем:"
)
DEFAULT_CLOSING = (
    "Остальные условия {contract_genitive}, не затронутые настоящим Протоколом разногласий, "
    "сохраняются без изменений.",
    "Настоящий Протокол разногласий вступает в силу с даты его подписания обеими Сторонами и является "
    "неотъемлемой частью {contract_genitive}.",
    "Настоящий Протокол разногласий составлен в двух экземплярах, имеющих одинаковую юридическую силу, "
    "по одному для каждой из Сторон.",
)
DEFAULT_APPENDICES_ITEM = "Приложения: {appendices}."

# Подстановки, которые понимают шаблоны (documentation/PROTOCOL_FORMAT.md, раздел 7).
PREAMBLE_FIELDS = {
    "first_party": "сторона, названная в договоре первой, с «именуемое в дальнейшем …»",
    "second_party": "вторая сторона",
    "buyer_intro": "покупатель с «именуемое в дальнейшем «Покупатель»»",
    "supplier_intro": "поставщик с «именуемый в дальнейшем «Поставщик»»",
    "contract": "«Договору поставки № 12/2026 от 31.07.2026»",
    "contract_kind": "вид договора: «поставки»",
    "contract_number": "номер договора",
    "contract_date": "дата договора: «31.07.2026»",
}
CLOSING_FIELDS = {
    "contract_genitive": "«Договора поставки № 12/2026»",
    "contract_kind": "вид договора",
    "contract_number": "номер договора",
    "contract_date": "дата договора",
}
APPENDICES_FIELDS = {"appendices": "перечень приложений через точку с запятой"}


def check_placeholders(text: str, allowed: dict[str, str]) -> None:
    """Разрешены только известные подстановки {имя}: без позиционных, атрибутов и индексов."""
    try:
        parts = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise ValueError(f"Непарные фигурные скобки: {exc}") from exc
    for _, name, spec, conversion in parts:
        if name is None:
            continue
        if name not in allowed or spec or conversion:
            known = ", ".join("{" + key + "}" for key in allowed)
            raise ValueError(f"Неизвестная подстановка {{{name}}}. Допустимы: {known}.")


class ProtocolTemplate(BaseModel):
    """Шаблон протокола из админки: тексты с подстановками и оформление по умолчанию."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=DEFAULT_TITLE, min_length=1, max_length=200)
    preamble: str = Field(default=DEFAULT_PREAMBLE, min_length=1, max_length=3000)
    closing_title: str = Field(default="Заключительные положения", max_length=200)
    closing_items: list[str] = Field(default_factory=lambda: list(DEFAULT_CLOSING), max_length=10)
    appendices_item: str = Field(default=DEFAULT_APPENDICES_ITEM, min_length=1, max_length=300)
    include_agreed_column: bool = True
    # Фирменный бланк: шапка организации на первой странице, контакты внизу. По умолчанию нет.
    letterhead: bool = False
    table_font_size: float = Field(default=10, ge=8, le=12)

    @field_validator("preamble")
    @classmethod
    def _preamble_fields(cls, value: str) -> str:
        check_placeholders(value, PREAMBLE_FIELDS)
        return value

    @field_validator("closing_items")
    @classmethod
    def _closing_fields(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 2000:
                raise ValueError("Пункт заключительных положений длиннее 2000 символов.")
            check_placeholders(item, CLOSING_FIELDS)
        return [item for item in value if item.strip()]

    @field_validator("appendices_item")
    @classmethod
    def _appendices_fields(cls, value: str) -> str:
        check_placeholders(value, APPENDICES_FIELDS)
        return value


def _contract_genitive(kind: str, number: str) -> str:
    return f"Договора {kind.strip()} №{NBSP}{number.strip() or '____'}".replace("  ", " ")


def render_preamble(
    template: ProtocolTemplate,
    buyer_intro: str,
    supplier_intro: str,
    contract: str,
    supplier_first: bool = False,
    kind: str = "",
    number: str = "",
    contract_date: date | None = None,
) -> str:
    first, second = (supplier_intro, buyer_intro) if supplier_first else (buyer_intro, supplier_intro)
    return template.preamble.format_map(
        {
            "first_party": first,
            "second_party": second,
            "buyer_intro": buyer_intro,
            "supplier_intro": supplier_intro,
            "contract": contract,
            "contract_kind": kind.strip(),
            "contract_number": number.strip(),
            "contract_date": short_date(contract_date) if contract_date else "",
        }
    )


def render_closing(
    template: ProtocolTemplate,
    kind: str,
    number: str,
    contract_date: date | None = None,
    appendices: Sequence[str] = (),
) -> list[str]:
    values = {
        "contract_genitive": _contract_genitive(kind, number),
        "contract_kind": kind.strip(),
        "contract_number": number.strip(),
        "contract_date": short_date(contract_date) if contract_date else "",
    }
    items = [item.format_map(values) for item in template.closing_items]
    listed = [item.strip() for item in appendices if item.strip()]
    if listed:
        items.append(template.appendices_item.format_map({"appendices": "; ".join(listed)}))
    return items


def default_preamble(buyer_intro: str, supplier_intro: str, title: str, supplier_first: bool = False) -> str:
    return render_preamble(ProtocolTemplate(), buyer_intro, supplier_intro, title, supplier_first)


def default_closing_items(kind: str, number: str, appendices: Sequence[str] = ()) -> list[str]:
    return render_closing(ProtocolTemplate(), kind, number, None, appendices)


@dataclass
class ContractParty:
    full_name: str
    short_name: str = ""
    representative: str = ""
    basis: str = ""
    signer: str = ""


@dataclass
class ProtocolDraft:
    """То, что известно о договоре и сторонах: из этого собираются данные протокола по умолчанию."""

    contract_kind: str
    contract_number: str
    contract_date: date | None
    city: str
    buyer: ContractParty
    supplier: ContractParty
    rows: list[ProtocolRow]
    protocol_date: date | None = None
    supplier_first: bool = False
    include_agreed_column: bool = True
    appendices: list[str] = field(default_factory=list)
    letterhead: Letterhead = field(default_factory=Letterhead)
    title: str = DEFAULT_TITLE


def build_protocol_data(draft: ProtocolDraft, template: ProtocolTemplate | None = None) -> ProtocolData:
    template = template or ProtocolTemplate()
    title = contract_title(draft.contract_kind, draft.contract_number, draft.contract_date)
    return ProtocolData(
        title=draft.title,
        subtitle=f"к {title}",
        city=draft.city,
        date=draft.protocol_date or draft.contract_date,
        preamble=render_preamble(
            template,
            party_intro(draft.buyer.full_name, "Покупатель", draft.buyer.representative, draft.buyer.basis),
            party_intro(
                draft.supplier.full_name, "Поставщик", draft.supplier.representative, draft.supplier.basis
            ),
            title,
            draft.supplier_first,
            draft.contract_kind,
            draft.contract_number,
            draft.contract_date,
        ),
        include_agreed_column=draft.include_agreed_column,
        rows=draft.rows,
        closing_title=template.closing_title,
        closing_items=render_closing(
            template, draft.contract_kind, draft.contract_number, draft.contract_date, draft.appendices
        ),
        buyer=SignatureParty(
            heading="ПОКУПАТЕЛЬ:",
            name=draft.buyer.short_name or draft.buyer.full_name,
            signer=draft.buyer.signer,
        ),
        supplier=SignatureParty(
            heading="ПОСТАВЩИК:",
            name=draft.supplier.short_name or draft.supplier.full_name,
            signer=draft.supplier.signer,
        ),
        letterhead=draft.letterhead,
        contract_number=draft.contract_number,
    )
