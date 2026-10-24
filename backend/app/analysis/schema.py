"""Схема ответа модели (documentation/ARCHITECTURE.md, раздел 4.3).

Модели pydantic — единственный источник: из них строится JSON Schema для провайдера (structured outputs,
--output-schema у Codex), и ими же проверяется ответ. Схема строгая: у каждого объекта перечислены все поля
в required и запрещены лишние (additionalProperties: false) — так требует strict-режим OpenAI.
"""

import copy
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["high", "medium", "low"]
Party = Literal["buyer", "supplier"]
Procurement = Literal["commercial", "223fz", "44fz", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuyerInfo(_Strict):
    """Покупатель по преамбуле договора."""

    name: str | None = Field(description="полное наименование покупателя, как в договоре")
    representative: str | None = Field(
        description="кто подписывает, в родительном падеже: «генерального директора Иванова И.И.»"
    )
    basis: str | None = Field(
        description="основание полномочий в родительном падеже: «Устава», «доверенности № 5»"
    )


class ContractInfo(_Strict):
    """Сведения о договоре. Чего нет в тексте — null."""

    kind: str | None = Field(description="вид договора в родительном падеже: «поставки»")
    number: str | None = Field(description="номер договора, как в тексте")
    date: str | None = Field(description="дата договора в формате ГГГГ-ММ-ДД")
    city: str | None = Field(description="город заключения договора")
    parties_order: list[Party] = Field(description="порядок сторон в преамбуле")
    buyer: BuyerInfo
    our_role: Literal["supplier", "buyer", "other", "unknown"] = Field(
        description="роль нашей организации: supplier — мы Поставщик и продаём товар"
    )
    procurement: Procurement = Field(description="режим закупки по тексту договора")

    @field_validator("date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        """Модель иногда пишет дату как в договоре (31.07.2026): приводим к ГГГГ-ММ-ДД, иначе — null."""
        if value is None:
            return None
        text = value.strip()
        match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        try:
            if match:
                return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None


class Approval(_Strict):
    """Можно ли согласовывать договор без правок."""

    allowed: bool = Field(
        description="false, если есть стоп-факторы, комплект неполный или режим закупки неизвестен"
    )
    reasons: list[str]


class Confidence(_Strict):
    """Уверенность в анализе и что на неё влияет."""

    level: Severity
    reasons: list[str]


class Risk(_Strict):
    """Риск для поставщика: факт (цитата), интерпретация, рекомендация и новая редакция."""

    id: str = Field(description="R1, R2, … по порядку")
    clauses: list[str] = Field(
        description="номера пунктов, как в договоре; у нового пункта — предлагаемый номер"
    )
    block_ids: list[str] = Field(description="метки блоков договора, откуда взята цитата")
    is_new_clause: bool = Field(description="true — пункта в договоре нет, предлагается новый")
    quote: str = Field(
        description="дословный фрагмент договора без сокращений; у нового пункта — пустая строка"
    )
    title: str = Field(description="коротко, в чём риск")
    explanation: str = Field(description="чем грозит поставщику, простыми словами для менеджера по продажам")
    severity: Severity
    stop_factor: bool = Field(description="true — с этим условием договор нельзя подписывать без исправления")
    category: str = Field(description="тема: оплата, поставка, приёмка, ответственность, споры и т. п.")
    recommendation: str = Field(description="чего добиться и на какой компромисс можно согласиться")
    proposed_wording: str | None = Field(
        description="полный текст пункта в новой редакции, начинается с номера пункта; null — правка не нужна"
    )


class Obligation(_Strict):
    """Обязательство одной из сторон и его срок."""

    party: Party
    obligation: str
    deadline: str | None = Field(description="срок или событие, от которого он считается")
    clauses: list[str]
    block_ids: list[str]


class Contradiction(_Strict):
    """Противоречие внутри договора или с документами, на которые он ссылается."""

    description: str
    clauses: list[str]
    block_ids: list[str]


class MissingItem(_Strict):
    """Недостающий документ или сведения."""

    item: str
    reason: str


class FavorableTerm(_Strict):
    """Условие, выгодное поставщику."""

    description: str
    clauses: list[str]
    block_ids: list[str]
    quote: str


class SpecialistCheck(_Strict):
    """Что стоит проверить специалисту."""

    specialist: Literal["lawyer", "finance", "production", "security"]
    what: str


class AnalysisAnswer(_Strict):
    """Ответ модели: итоговый отчёт по договору в разделах промпта администратора."""

    contract: ContractInfo
    summary: str = Field(description="общий вывод в 2–3 предложениях")
    approval: Approval
    confidence: Confidence
    top_risks: list[str] = Field(description="идентификаторы до пяти главных рисков, по убыванию важности")
    risks: list[Risk]
    obligations: list[Obligation]
    contradictions: list[Contradiction]
    missing: list[MissingItem]
    favorable: list[FavorableTerm]
    specialist_checks: list[SpecialistCheck]

    @model_validator(mode="after")
    def _unique_risk_ids(self) -> "AnalysisAnswer":
        ids = [risk.id for risk in self.risks]
        if any(not risk_id.strip() for risk_id in ids):
            raise ValueError("у риска пустой id")
        duplicates = sorted({risk_id for risk_id in ids if ids.count(risk_id) > 1})
        if duplicates:
            raise ValueError(f"повторяются id рисков: {', '.join(duplicates)}")
        return self


def _strip_titles(node: Any) -> Any:
    """Убрать служебные title, которые pydantic ставит каждой схеме. Поле риска title остаётся."""
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key in ("properties", "$defs") and isinstance(value, dict):
            result[key] = {name: _strip_titles(schema) for name, schema in value.items()}
        else:
            result[key] = _strip_titles(value)
    return result


def _check_strict(node: Any, path: str = "#") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties", {})
            if node.get("additionalProperties") is not False:
                raise AssertionError(f"{path}: нет additionalProperties: false")
            if sorted(node.get("required", [])) != sorted(properties):
                raise AssertionError(f"{path}: не все поля в required")
        for key, value in node.items():
            _check_strict(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _check_strict(item, f"{path}/{index}")


_SCHEMA: dict[str, Any] | None = None


def response_schema() -> dict[str, Any]:
    """JSON Schema ответа для провайдера (копия: вызывающий может её менять)."""
    global _SCHEMA
    if _SCHEMA is None:
        schema = _strip_titles(AnalysisAnswer.model_json_schema())
        _check_strict(schema)
        _SCHEMA = schema
    return copy.deepcopy(_SCHEMA)
