"""Данные протокола. Генератор ничего не вычисляет сам: всё, что попадает в документ, лежит здесь."""

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel, Field


class ProtocolRow(BaseModel):
    # «п. 9.2», «пп. 6.1, 6.3 и 6.5», «п. 9.9 (новый пункт)»
    clause: str = Field(max_length=200)
    # Перевод строки в тексте — новый абзац в ячейке.
    buyer_text: str = Field(default="", max_length=20000)
    supplier_text: str = Field(default="", max_length=20000)
    agreed_text: str = Field(default="", max_length=20000)


class SignatureParty(BaseModel):
    heading: str = Field(max_length=100)
    name: str = Field(default="", max_length=500)
    # «И.И. Иванов»; пусто — линия для подписи без расшифровки.
    signer: str = Field(default="", max_length=200)


class Letterhead(BaseModel):
    enabled: bool = False
    # Контакты в нижнем колонтитуле (адрес, телефон, e-mail).
    footer: str = Field(default="", max_length=500)


class ProtocolData(BaseModel):
    title: str = Field(default="ПРОТОКОЛ РАЗНОГЛАСИЙ", max_length=200)
    subtitle: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=100)
    date: dt.date | None = None
    preamble: str = Field(default="", max_length=5000)
    include_agreed_column: bool = True
    # Размер шрифта таблицы: 10 pt по умолчанию; 9 pt — плотнее.
    table_font_size: float = Field(default=10, ge=8, le=12)
    rows: list[ProtocolRow] = Field(default_factory=list, max_length=500)
    closing_title: str = Field(default="Заключительные положения", max_length=200)
    closing_items: list[str] = Field(default_factory=list, max_length=20)
    buyer: SignatureParty = Field(default_factory=lambda: SignatureParty(heading="ПОКУПАТЕЛЬ:"))
    supplier: SignatureParty = Field(default_factory=lambda: SignatureParty(heading="ПОСТАВЩИК:"))
    letterhead: Letterhead = Field(default_factory=Letterhead)
    # Номер договора нужен только для угла поворота печати: при повторном выпуске он тот же.
    contract_number: str = Field(default="", max_length=100)


@dataclass
class ProtocolImages:
    """Картинки организации (PNG/JPEG) из таблицы organizations: шапка бланка, подпись, печать."""

    header: bytes | None = None
    signature: bytes | None = None
    stamp: bytes | None = None
