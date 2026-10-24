"""Проверка ответа модели по договору (documentation/ARCHITECTURE.md, раздел 4.4).

- Цитаты ищутся сначала в блоках, на которые сослалась модель, потом во всём договоре: точно (после
  нормализации), затем нечётко (rapidfuzz, partial_ratio ≥ 90). Итог — quote_status.
- Номера пунктов сверяются с разметкой, метки блоков — со списком блоков; ссылки проверяются во всех
  разделах ответа, а не только в рисках.
- Ссылка на приложение («Приложение № 1», «Спецификация») верна, если в договоре есть блок, с которого
  это приложение начинается.
- Страницы система берёт по меткам блоков (у PDF и сканов), модели их указывать не нужно.

Результат хранится в проверке рядом с ответом модели и показывается менеджеру.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.analysis.schema import AnalysisAnswer

FUZZY_THRESHOLD = 90
# Короткие цитаты нечётко не ищутся: «0,5%» похоже на половину договора.
MIN_FUZZY_LENGTH = 12

_QUOTES = str.maketrans(
    {"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"', "'": '"', "’": '"', "‘": '"'}
)
_DASHES = str.maketrans({"—": "-", "–": "-", "‑": "-", "‒": "-", "−": "-"})
_SPACES = re.compile(r"[\s\u00a0\u2007\u202f\u2009\u200b]+")


def normalize(text: str) -> str:
    """Для сравнения цитат: регистр, ё/е, кавычки, тире, пробелы вокруг знаков препинания и процентов."""
    text = text.lower().replace("ё", "е").translate(_QUOTES).translate(_DASHES)
    text = text.replace("…", " ").replace("...", " ")
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\s+([%,.;:!?)\]])", r"\1", text)
    text = re.sub(r"([(\[№])\s+", r"\1", text)
    return text.strip()


# «п. 9.2», «пп. 6.1», «п.п. 6.1», «пункт 4», «подпункт 6.1.2»: слово снимается, только если за ним номер.
_POINT_WORD = r"(?:под)?пункт[а-я]*|подп\.?|п\.?\s*п\.?|п\.?"
_POINT_PREFIX = re.compile(rf"^(?:{_POINT_WORD})\s*(?=\d)")
_POINT = re.compile(rf"\b(?:{_POINT_WORD})\s*(\d+(?:\.\d+)*)")
_NUMBER_SIGN = r"\s*(?:№|no\.?|n)?\s*"
# «Приложение № 1», «Приложение 1», «прил. 1», «Приложения №1 «Спецификация»»: номер обязателен.
_APPENDIX = re.compile(rf"\bприл(?:ожени[а-я]*)?\.?{_NUMBER_SIGN}(\d+(?:\.\d+)*)")
_SPECIFICATION = re.compile(rf"\bспецификаци[а-я]*(?:{_NUMBER_SIGN}(\d+(?:\.\d+)*))?")
# Блок, который начинается с названия приложения: «Приложение № 1 …», «СПЕЦИФИКАЦИЯ», «Спецификация № 2 …».
_DOCUMENT_START = re.compile(rf"^(приложение|спецификация)(?![а-я]){_NUMBER_SIGN}(\d+(?:\.\d+)*)?\.?\s*")
_DOCUMENT_KIND = re.compile(r"^(приложение|спецификация)")
_TO_CONTRACT = re.compile(r"к\s")


def normalize_clause(clause: str) -> str:
    """Номер пункта для сравнения: «п. 9.2.» → «9.2».

    Приложение и спецификация сводятся к одному виду: «Приложение № 1», «прил. 1» и «Приложение №1
    «Спецификация»» → «приложение1», «Спецификация» → «спецификация». Пункт внутри них идёт уровнем ниже:
    «п. 3 Приложения № 1» → «приложение1.3». Голого номера приложение не даёт: «Приложение 1» — не пункт 1.
    """
    text = clause.strip().lower().replace("ё", "е")
    document = _APPENDIX.search(text)
    if document:
        name = f"приложение{document.group(1)}"
    else:
        document = _SPECIFICATION.search(text)
        name = f"спецификация{document.group(1) or ''}" if document else ""
    if document:
        point = _POINT.search(f"{text[: document.start()]} {text[document.end() :]}")
        return f"{name}.{point.group(1)}" if point else name
    text = _POINT_PREFIX.sub("", text)
    return text.replace(" ", "").rstrip(".")


def _document_names(block: Mapping[str, Any]) -> tuple[set[str], bool] | None:
    """Названия приложения, с которого начинается блок, и начинает ли блок само приложение.

    Смотрится первая строка до «;»: в ней «Приложение № 1 – Спецификация» — два названия одного документа.
    Приложение начинает заголовок или строка, где после названия ничего нет или идёт «к договору …».
    Строка перечня «Приложение № 1 – Спецификация.» только связывает названия, а упоминание в середине
    текста («указаны в Приложении № 1») не берётся вовсе: такого приложения в файле может не быть.
    """
    text = str(block.get("text", "")).strip().lower().replace("ё", "е")
    line = re.split(r"[\n;]", text, maxsplit=1)[0].strip()
    start = _DOCUMENT_START.match(line)
    if not start:
        return None
    names = {f"{start.group(1)}{start.group(2) or ''}"}
    names.update(f"приложение{match.group(1)}" for match in _APPENDIX.finditer(line))
    names.update(f"спецификация{match.group(1) or ''}" for match in _SPECIFICATION.finditer(line))
    rest = line[start.end() :]
    opens = block.get("kind") == "heading" or not rest or bool(_TO_CONTRACT.match(rest))
    return names, opens


@dataclass
class _Span:
    start: int
    end: int
    block_id: str


class ContractIndex:
    """Блоки договора для поиска цитат и проверки ссылок."""

    def __init__(self, blocks: Iterable[Mapping[str, Any]]) -> None:
        self.blocks = list(blocks)
        self.by_id = {str(block["id"]): block for block in self.blocks}
        self.normalized = {
            block_id: normalize(str(block.get("text", ""))) for block_id, block in self.by_id.items()
        }
        parts: list[str] = []
        self.spans: list[_Span] = []
        position = 0
        for block_id, text in self.normalized.items():
            self.spans.append(_Span(position, position + len(text), block_id))
            parts.append(text)
            position += len(text) + 1
        self.full_text = " ".join(parts)
        self.clauses = {
            normalize_clause(str(block["clause"])) for block in self.blocks if block.get("clause")
        }
        self.documents = self._attached_documents()

    def _attached_documents(self) -> set[str]:
        """Приложения, которые есть в файле договора, в виде normalize_clause.

        По номерам пунктов их не найти: на заголовке приложения разметка начинает нумерацию заново.
        """
        known: set[str] = set()
        groups: list[set[str]] = []
        for block in self.blocks:
            found = _document_names(block)
            if found is None:
                continue
            names, opens = found
            groups.append(names)
            if opens:
                known |= names
        # Перечень «Приложение № 1 – Спецификация»: если спецификация в файле есть, есть и Приложение № 1.
        changed = True
        while changed:
            changed = False
            for names in groups:
                if names & known and not names <= known:
                    known |= names
                    changed = True
        # «Спецификация» и «Приложение» без номера — любая спецификация или любое приложение из файла.
        known |= {match.group(1) for name in known if (match := _DOCUMENT_KIND.match(name))}
        return known

    def known_block(self, block_id: str) -> bool:
        return block_id in self.by_id

    def known_clause(self, clause: str) -> bool:
        number = normalize_clause(clause)
        if _DOCUMENT_KIND.match(number):
            # Пункт приложения («п. 3 Приложения № 1») не сверяется: у спецификации-таблицы номеров нет,
            # а свои номера приложений в разметке смешаны с номерами договора.
            return any(number == name or number.startswith(name + ".") for name in self.documents)
        # «9» — раздел, в котором есть пункты 9.1, 9.2: это тоже существующая ссылка.
        return number in self.clauses or any(known.startswith(number + ".") for known in self.clauses)

    def pages(self, block_ids: Iterable[str]) -> list[int]:
        pages = {self.by_id[block_id].get("page") for block_id in block_ids if block_id in self.by_id}
        return sorted(page for page in pages if isinstance(page, int))

    def _blocks_between(self, start: int, end: int) -> list[str]:
        return [span.block_id for span in self.spans if span.start < end and start < span.end]

    def locate(self, quote: str, cited: list[str]) -> tuple[str, list[str]]:
        """Найти цитату: (exact | fuzzy | not_found | empty, метки блоков, где она нашлась)."""
        # Точка в конце цитаты не обязана совпадать: в договоре там может быть запятая и продолжение.
        needle = normalize(quote).rstrip(" .")
        if not needle:
            return "empty", []
        cited = [block_id for block_id in cited if block_id in self.by_id]
        cited_text = " ".join(self.normalized[block_id] for block_id in cited)
        if cited and needle in cited_text:
            return "exact", [block_id for block_id in cited if self.normalized[block_id]] or cited
        position = self.full_text.find(needle)
        if position >= 0:
            return "exact", self._blocks_between(position, position + len(needle))
        if len(needle) >= MIN_FUZZY_LENGTH:
            if cited and fuzz.partial_ratio(needle, cited_text) >= FUZZY_THRESHOLD:
                return "fuzzy", cited
            alignment = fuzz.partial_ratio_alignment(needle, self.full_text, score_cutoff=FUZZY_THRESHOLD)
            if alignment is not None:
                return "fuzzy", self._blocks_between(alignment.dest_start, alignment.dest_end)
        return "not_found", []


@dataclass
class CheckedAnswer:
    """Ответ модели и результаты проверки: то, что сохраняется в checks.result."""

    answer: dict[str, Any]
    risk_checks: dict[str, dict[str, Any]]
    refs: dict[str, list[dict[str, Any]]]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "risk_checks": self.risk_checks,
            "refs": self.refs,
            "warnings": self.warnings,
        }


def _reference(index: ContractIndex, clauses: list[str], block_ids: list[str]) -> dict[str, Any]:
    known = [block_id for block_id in block_ids if index.known_block(block_id)]
    return {
        "pages": index.pages(known),
        "unknown_block_ids": [block_id for block_id in block_ids if not index.known_block(block_id)],
        "unknown_clauses": [clause for clause in clauses if not index.known_clause(clause)],
    }


def check_answer(answer: AnalysisAnswer, blocks: Iterable[Mapping[str, Any]]) -> CheckedAnswer:
    index = ContractIndex(blocks)
    warnings: list[str] = []
    risk_checks: dict[str, dict[str, Any]] = {}
    for risk in answer.risks:
        reference = _reference(index, [] if risk.is_new_clause else risk.clauses, risk.block_ids)
        cited = [block_id for block_id in risk.block_ids if index.known_block(block_id)]
        if risk.is_new_clause and not risk.quote.strip():
            status, found = "new_clause", []
        else:
            status, found = index.locate(risk.quote, cited)
        if found:
            reference["pages"] = index.pages(found)
        risk_checks[risk.id] = {"quote_status": status, "found_block_ids": found, **reference}
        if status == "not_found":
            warnings.append(
                f"{risk.id}: цитата не найдена в договоре — проверьте её перед отправкой протокола."
            )
        elif status == "empty":
            warnings.append(f"{risk.id}: у риска нет цитаты из договора.")
        elif status == "fuzzy":
            warnings.append(f"{risk.id}: цитата совпадает с договором неточно — сверьте её с текстом.")
        if reference["unknown_clauses"]:
            numbers = ", ".join(reference["unknown_clauses"])
            warnings.append(f"{risk.id}: в договоре нет пунктов {numbers}.")

    refs: dict[str, list[dict[str, Any]]] = {}
    for name in ("obligations", "contradictions", "favorable"):
        items = getattr(answer, name)
        refs[name] = [_reference(index, item.clauses, item.block_ids) for item in items]
    for item, reference in zip(answer.favorable, refs["favorable"], strict=True):
        cited = [block_id for block_id in item.block_ids if index.known_block(block_id)]
        reference["quote_status"], found = index.locate(item.quote, cited)
        if found:
            reference["pages"] = index.pages(found)

    data = answer.model_dump(mode="json")
    risk_ids = {risk.id for risk in answer.risks}
    unknown_top = [risk_id for risk_id in answer.top_risks if risk_id not in risk_ids]
    if unknown_top:
        warnings.append(f"Среди главных рисков указаны несуществующие: {', '.join(unknown_top)}.")
    # Порядок главных рисков — как у модели; повторы и несуществующие убираются, не больше пяти.
    data["top_risks"] = list(dict.fromkeys(risk_id for risk_id in answer.top_risks if risk_id in risk_ids))[
        :5
    ]
    return CheckedAnswer(answer=data, risk_checks=risk_checks, refs=refs, warnings=warnings)
