"""Сборка запроса к модели (documentation/ARCHITECTURE.md, раздел 4.2).

system: промпт администратора, затем фиксированная часть системы — её правка промпта не затрагивает.
user: сведения о проверке и договор по блокам. Имя файла и наши реквизиты не отправляются.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.analysis.default_prompt import DEFAULT_ADMIN_PROMPT
from app.analysis.fixed_part import FIXED_PART
from app.analysis.providers.base import AnalysisRequest
from app.analysis.schema import response_schema

SCHEMA_NAME = "contract_analysis"


PROCUREMENT_LABELS = {
    "commercial": "коммерческая сделка",
    "223fz": "закупка по 223-ФЗ",
    "44fz": "госконтракт по 44-ФЗ",
    "unknown": "не указан менеджером",
}


@dataclass
class CheckInfo:
    """Сведения о проверке, которые уходят в запрос вместе с договором."""

    procurement: str = "unknown"
    comment: str = ""
    ocr_used: bool = False
    page_count: int | None = None
    # Предупреждения чтения документа (скрытый текст, правки, качество распознавания). Их пишет система.
    warnings: list[str] = field(default_factory=list)
    # Наша сторона по реквизитам (app/analysis/requisites.py): только роль, без самих реквизитов.
    side_note: str | None = None
    # supplier, buyer, other, unknown — для сведений об анализе; None — сверка не выполнялась.
    our_role: str | None = None


def prompt_fingerprint(admin_prompt: str) -> str:
    """Короткий отпечаток промпта администратора: по нему видно, с какой версией сделан анализ."""
    return hashlib.sha256(admin_prompt.encode("utf-8")).hexdigest()[:12]


def _block_line(block: Mapping[str, Any]) -> str:
    marks = []
    if block.get("page"):
        marks.append(f"стр. {block['page']}")
    if block.get("clause"):
        marks.append(f"п. {block['clause']}")
    if block.get("kind") == "heading":
        marks.append("заголовок")
    elif block.get("kind") == "cell":
        marks.append("таблица")
    text = " / ".join(part.strip() for part in str(block.get("text", "")).splitlines() if part.strip())
    prefix = f"[{block['id']}]"
    return f"{prefix} ({', '.join(marks)}) {text}" if marks else f"{prefix} {text}"


def render_contract(blocks: Iterable[Mapping[str, Any]]) -> str:
    return "\n".join(_block_line(block) for block in blocks)


def render_info(info: CheckInfo) -> str:
    lines = [
        "СВЕДЕНИЯ О ПРОВЕРКЕ",
        "- Анализ — в интересах Поставщика: наша организация продаёт товар.",
        *([f"- {info.side_note}"] if info.side_note else []),
        f"- Режим закупки: {PROCUREMENT_LABELS.get(info.procurement, PROCUREMENT_LABELS['unknown'])}.",
        "- В проверке один файл. Документы, на которые договор ссылается, но которых нет в тексте ниже, "
        "считай недостающими.",
    ]
    if info.ocr_used:
        pages = f", страниц: {info.page_count}" if info.page_count else ""
        lines.append(f"- Текст распознан со скана{pages}: возможны ошибки распознавания в словах и цифрах.")
    for warning in info.warnings:
        lines.append(f"- При чтении документа: {warning}")
    comment = info.comment.strip()
    lines.append(
        f"- Параметры сделки и что учесть (от менеджера): {comment}"
        if comment
        else "- Комментария менеджера нет."
    )
    return "\n".join(lines)


def build_request(
    blocks: Iterable[Mapping[str, Any]], info: CheckInfo, admin_prompt: str = DEFAULT_ADMIN_PROMPT
) -> AnalysisRequest:
    system = f"{admin_prompt.strip()}\n\n{FIXED_PART}"
    user = f"{render_info(info)}\n\nДОГОВОР\n{render_contract(blocks)}"
    return AnalysisRequest(system=system, user=user, schema=response_schema(), schema_name=SCHEMA_NAME)


def with_feedback(request: AnalysisRequest, feedback: str) -> AnalysisRequest:
    """Повторный запрос после ответа, который не прошёл проверку: договор тот же, в конце — что исправить."""
    user = (
        f"{request.user}\n\nПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРИНЯТ\n{feedback}\n"
        "Дай ответ заново целиком, одним JSON-объектом строго по схеме."
    )
    return AnalysisRequest(
        system=request.system, user=user, schema=request.schema, schema_name=request.schema_name
    )
