"""Сборка абзацев из строк PDF и распознанных сканов.

В PDF и на скане нет абзацев — есть строки с координатами. Абзац начинается, если:
- строка начинается с номера пункта, буквы перечня или маркера списка;
- промежуток между строками заметно больше обычного межстрочного;
- предыдущая строка закончилась точкой (двоеточием, точкой с запятой), а эта начинается с красной строки
  или предыдущая не дошла до правого края;
- строка или предыдущая строка похожи на заголовок (короткая, заглавными буквами).
Колонтитулы («Страница | 3», строки, повторяющиеся на большинстве страниц) убираются.
Координаты строк — в пунктах PDF (1/72 дюйма); распознанные строки пересчитываются в них же.
"""

import itertools
import re
import statistics
from dataclasses import dataclass

from app.documents.segment import starts_new_item

PAGE_NUMBER_RE = re.compile(
    r"^[\W_]*(?:стр(?:аница)?\.?|page)?[\W_]*\d{1,4}(?:[\W_]*(?:из|of|/)[\W_]*\d{1,4})?[\W_]*$",
    re.IGNORECASE,
)
SENTENCE_END = (".", ":", ";", "!", "?", "»")
HEADER_ZONE = 0.07
FOOTER_ZONE = 0.93


@dataclass
class Line:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int
    page_width: float
    page_height: float
    # Блок и абзац по разбору Tesseract; для PDF с текстом — None.
    block: int | None = None
    par: int | None = None


@dataclass
class LayoutStats:
    line_height: float
    paragraph_gap: float
    left_margin: float
    text_width: float


def _caps_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(ch.isupper() for ch in letters) / len(letters)


def looks_like_heading(text: str) -> bool:
    return 3 <= len(text) <= 150 and _caps_ratio(text) >= 0.85 and sum(ch.isalpha() for ch in text) >= 4


def _zone_key(line: Line) -> str | None:
    """Ключ строки для поиска колонтитулов: цифры заменены, учитывается зона страницы."""
    position = line.top / line.page_height if line.page_height else 0.5
    if HEADER_ZONE < position < FOOTER_ZONE:
        return None
    zone = "top" if position <= HEADER_ZONE else "bottom"
    return zone + ":" + re.sub(r"\d+", "#", " ".join(line.text.lower().split()))


def remove_headers_and_footers(lines: list[Line], page_count: int) -> list[Line]:
    pages_by_key: dict[str, set[int]] = {}
    for line in lines:
        key = _zone_key(line)
        if key:
            pages_by_key.setdefault(key, set()).add(line.page)
    threshold = max(2, page_count // 2)
    repeated = {key for key, pages in pages_by_key.items() if page_count >= 3 and len(pages) >= threshold}
    result = []
    for line in lines:
        key = _zone_key(line)
        if key and (PAGE_NUMBER_RE.match(line.text.strip()) or key in repeated):
            continue
        result.append(line)
    return result


def layout_stats(lines: list[Line]) -> LayoutStats:
    heights = [line.bottom - line.top for line in lines if line.bottom > line.top]
    line_height = statistics.median(heights) if heights else 10.0
    # Обычный промежуток между строками одного абзаца зависит от межстрочного интервала документа.
    gaps = [
        current.top - prev.bottom
        for prev, current in itertools.pairwise(lines)
        if prev.page == current.page and current.top > prev.bottom
    ]
    usual_gap = statistics.median(gaps) if gaps else 0.3 * line_height
    lefts = sorted(line.x0 for line in lines) or [0.0]
    rights = sorted(line.x1 for line in lines) or [1.0]
    left_margin = statistics.median(lefts[: max(1, len(lefts) // 3)])
    right_margin = statistics.median(rights[-max(1, len(rights) // 3) :])
    return LayoutStats(
        line_height=line_height,
        paragraph_gap=usual_gap + 0.5 * line_height,
        left_margin=left_margin,
        text_width=max(1.0, right_margin - left_margin),
    )


def lines_to_paragraphs(lines: list[Line], stats: LayoutStats) -> list[tuple[str, int]]:
    """Абзацы из строк в порядке чтения: (текст, номер страницы первой строки)."""
    paragraphs: list[tuple[list[str], int]] = []
    previous: Line | None = None
    for line in lines:
        text = " ".join(line.text.split())
        if not text:
            continue
        new = previous is None
        if previous is not None:
            prev_text = " ".join(previous.text.split())
            same_page = previous.page == line.page
            gap = line.top - previous.bottom if same_page else 0.0
            ended = prev_text.endswith(SENTENCE_END)
            indented = line.x0 - stats.left_margin > 0.03 * stats.text_width
            short_previous = previous.x1 < stats.left_margin + 0.8 * stats.text_width
            new_block = line.block is not None and (line.page, line.block) != (previous.page, previous.block)
            new_par = line.par is not None and (line.block, line.par) != (previous.block, previous.par)
            new = (
                new_block
                or (new_par and (ended or starts_new_item(text)))
                or starts_new_item(text)
                or (same_page and gap > stats.paragraph_gap)
                or (ended and (indented or short_previous))
                or looks_like_heading(text)
                or looks_like_heading(prev_text)
            )
        if new:
            paragraphs.append(([text], line.page))
        else:
            parts = paragraphs[-1][0]
            last = parts[-1]
            # Перенос по слогам: «пред-» + «приниматель» → «предприниматель».
            if last.endswith("-") and len(last) > 1 and last[-2].isalpha() and text[:1].islower():
                parts[-1] = last[:-1] + text
            else:
                parts.append(text)
        previous = line
    return [(" ".join(parts), page) for parts, page in paragraphs]
