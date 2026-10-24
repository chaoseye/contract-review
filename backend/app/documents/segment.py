"""Разметка абзацев на пункты договора: метки блоков, номер пункта, раздел.

Номер пункта берётся из автонумерации Word или из начала текста:
- «9.2», «9.2.», «9.2.1.», «1.1.Текст» — пункт, многоуровневый номер;
- «9.» — пункт верхнего уровня (обычно раздел). «9» без точки пунктом не считается: «31 июля», «100 рублей»;
- «Статья 5», «Раздел 3», «Глава 2» — раздел;
- «1)», «а)», «—», «•» — перечень внутри текущего пункта, номер пункта не меняется.
Абзацы без номера относятся к последнему найденному пункту. До первого пункта — преамбула (clause=None).
В таблицах номер пункта берётся только из длинного текста: короткое «1.» в ячейке — номер строки таблицы.
Заголовок «Приложение…» или «Спецификация…» начинает новую нумерацию.
Заголовок — стиль «Heading N» или короткий абзац заглавными буквами. Строка, которая начинается с метки
реквизита («ИНН …, КПП …», «ОГРНИП …», «ОКПО …»), заголовком не считается.
"""

import re
from collections import Counter

from app.documents.model import Block, BlockKind, RawParagraph

# Ссылка «п. 3.2 настоящего Договора» в начале абзаца — не номер пункта, поэтому префиксов «п.» нет.
NUMBER_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,5})(\.)?")
LABEL_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})*\.?$")
SECTION_RE = re.compile(r"^\s*(?:статья|раздел|глава)\s+(\d{1,3}|[IVXLC]{1,6})\b\.?", re.IGNORECASE)
ENUMERATOR_RE = re.compile(r"^\s*(?:\(?\d{1,2}\)|\(?[а-яa-z]\)|[—–\-•●▪■○◦∙·*]\s)", re.IGNORECASE)
APPENDIX_RE = re.compile(r"^\s*(?:приложение|спецификация)\b", re.IGNORECASE)
# Буквы в строках реквизитов заглавные, но это не заголовки. Число цифр не годится в признак:
# в заголовке приложения бывает номер госконтракта из 19 цифр или ИКЗ из 36.
REQUISITES_LINE_RE = re.compile(
    r"^\s*(?:инн|кпп|огрн|огрнип|окпо|октмо|окато|оквэд|окогу|окопф|окфс|бик|кбк|[ркл]/\s?с(?:ч(?:[её]т)?)?)"
    r"(?![^\W\d_])",
    re.IGNORECASE,
)
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
MAX_COMPONENT = 300
MIN_CELL_LETTERS = 20
DUPLICATE_WARNING = (
    "Номера пунктов повторяются: {numbers}. Проверьте нумерацию договора — ссылки могут быть неоднозначными."
)


def _roman_to_int(value: str) -> int | None:
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = ROMAN_VALUES.get(char)
        if current is None:
            return None
        total = total - current if current < previous else total + current
        previous = max(previous, current)
    return total or None


def parse_clause_number(text: str) -> str | None:
    """Номер пункта в начале текста или None."""
    section = SECTION_RE.match(text)
    if section:
        raw = section.group(1)
        number = int(raw) if raw.isdigit() else _roman_to_int(raw)
        return str(number) if number else None
    match = NUMBER_RE.match(text)
    if not match:
        return None
    number, trailing_dot = match.group(1), match.group(2)
    rest = text[match.end() :]
    if trailing_dot:
        # «1.1.Текст» — пункт; «1.1.5» сюда не попадёт: цифры съел бы номер.
        if rest[:1].isdigit():
            return None
    else:
        if rest and not rest[0].isspace():
            return None
        if "." not in number:
            # Одиночное число без точки — «31 июля», «100 рублей», а не пункт.
            return None
    components = number.split(".")
    if any(int(part) > MAX_COMPONENT for part in components) or int(components[0]) == 0:
        return None
    return number


def starts_new_item(text: str) -> bool:
    """Строка открывает новый пункт или элемент перечня (для сборки абзацев из строк PDF)."""
    return parse_clause_number(text) is not None or bool(ENUMERATOR_RE.match(text))


def _label_clause(label: str) -> str | None:
    """Номер пункта из метки автонумерации Word: «9.2.» → «9.2», «Статья 5.» → «5», «а)» → None."""
    if LABEL_NUMBER_RE.match(label):
        number = label.rstrip(".")
        if all(0 < int(part) <= MAX_COMPONENT for part in number.split(".")):
            return number
        return None
    section = SECTION_RE.match(label)
    if section:
        return parse_clause_number(label)
    return None


def is_heading(paragraph: RawParagraph) -> bool:
    text = paragraph.text
    if not text or paragraph.table is not None or len(text) > 150:
        return False
    if paragraph.heading_level is not None:
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 4 or REQUISITES_LINE_RE.match(text):
        return False
    return sum(ch.isupper() for ch in letters) / len(letters) >= 0.85


def _paragraph_clause(paragraph: RawParagraph) -> str | None:
    if paragraph.is_bullet:
        return None
    clause = _label_clause(paragraph.label) if paragraph.label else parse_clause_number(paragraph.text)
    if clause is not None and paragraph.table is not None:
        letters = sum(ch.isalpha() for ch in paragraph.text)
        if letters < MIN_CELL_LETTERS:
            return None
    return clause


def segment(paragraphs: list[RawParagraph]) -> tuple[list[Block], list[str]]:
    blocks: list[Block] = []
    current_clause: str | None = None
    current_section: str | None = None
    starts: Counter[str] = Counter()
    duplicates: set[str] = set()

    for paragraph in paragraphs:
        if not paragraph.text:
            # Пустой абзац с номером: счётчик Word уже учтён, показывать нечего.
            continue
        label = paragraph.label
        if label and paragraph.is_bullet:
            display = f"— {paragraph.text}"
        elif label:
            display = f"{label} {paragraph.text}"
        else:
            display = paragraph.text

        clause = _paragraph_clause(paragraph)
        heading = is_heading(paragraph)
        if heading and APPENDIX_RE.match(paragraph.text) and clause is None:
            # Приложение со своей нумерацией: прежние номера к нему не относятся.
            starts.clear()
            current_clause = None
        if clause is not None:
            current_clause = clause
            starts[clause] += 1
            if starts[clause] > 1:
                duplicates.add(clause)
        if heading:
            kind = BlockKind.HEADING
            current_section = display
        elif paragraph.table is not None:
            kind = BlockKind.CELL
        else:
            kind = BlockKind.PARAGRAPH

        blocks.append(
            Block(
                id=f"B{len(blocks) + 1:04d}",
                kind=kind,
                text=display,
                clause=current_clause,
                section=current_section,
                label=label,
                table=paragraph.table,
                page=paragraph.page,
            )
        )

    warnings = []
    if duplicates:
        ordered = sorted(duplicates, key=_sort_key)
        shown = ", ".join(ordered[:10]) + (" и другие" if len(ordered) > 10 else "")
        warnings.append(DUPLICATE_WARNING.format(numbers=shown))
    return blocks, warnings


def _sort_key(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in number.split("."))
