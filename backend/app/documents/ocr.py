"""Распознавание страниц без текстового слоя: pypdfium2 рендерит страницу, Tesseract распознаёт её.

Tesseract запускается как отдельная программа (stdin → TSV в stdout) и ничего не отправляет наружу.

Таблицы с линиями на скане: линии ищутся на картинке, ячейки из них строят те же функции pdfplumber,
что и у PDF с текстом. Распознавая страницу целиком, Tesseract пропускает короткие значения в ячейках,
перемешивает строки соседних колонок и путает подписи в две строки. Поэтому линии таблиц стираются,
ячейки распознаются отдельно, а остальной текст страницы — как раньше, без ячеек.
"""

import io
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pdfplumber.table import (
    Table,
    cells_to_tables,
    edges_to_intersections,
    intersections_to_cells,
    merge_edges,
)
from PIL import Image, ImageDraw

from app.documents.errors import ConversionError, ToolMissingError
from app.documents.process import run_process
from app.documents.text_lines import Line

DPI = 300
MAX_SIDE_PX = 5000
TESSERACT_TIMEOUT_SECONDS = 90
LANGUAGES = "rus+eng"
LOW_CONFIDENCE = 70.0

# Линии таблиц на картинке. Пиксель темнее RULE_DARK — часть линии. Размеры — в пунктах (1/72 дюйма).
RULE_DARK = 160
# Горизонтальная линия — не короче 1 см: такой непрерывной полосы нет ни у одной буквы.
H_RULE_MIN_PT = 28.0
# Вертикальная — не короче 6,7 мм, это выше любой буквы основного текста.
V_RULE_MIN_PT = 19.0
# Короткая перегородка между ячейками одной строки — от 3 мм, если оба конца упираются в горизонтальные линии.
V_DIVIDER_MIN_PT = 9.0
RULE_TOUCH_PT = 2.0
# Склейка линий и поиск пересечений — как у pdfplumber по умолчанию.
TABLE_TOLERANCE_PT = 3.0
EDGE_MIN_PT = 3.0
# Вырезка ячейки — с отступом внутрь от линий; вырезки ложатся столбиком с полями и промежутками.
CELL_INSET_PT = 1.0
CELL_PAD_PX = 20
CELL_GAP_PX = 40
MAX_STACK_PX = 20000
# Края стёртой линии светлее RULE_DARK, но Tesseract видит в них бледную рамку вокруг слов: светлые пиксели
# в полосе ±0,5 пт от линии тоже закрашиваются. Тёмные пиксели букв, касающихся линии, остаются.
RULE_HALO_PT = 0.5
_LIGHT_TO_WHITE = bytes(255 if value >= RULE_DARK else value for value in range(256))

WINDOWS_CANDIDATES = (Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),)

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Rule:
    """Непрерывная тёмная полоса в одной строке пикселей (горизонтальная) или в одном столбце (вертикальная).

    pos — y или x полосы, start и end — её начало и конец (end не входит), всё в пикселях.
    """

    pos: int
    start: int
    end: int


@dataclass
class OcrTable:
    top: float
    # Строки таблицы: текст ячеек слева направо; None — место занято объединённой ячейкой.
    rows: list[list[str | None]]


@dataclass
class OcrPage:
    lines: list[Line]
    mean_confidence: float | None
    tables: list[OcrTable] = field(default_factory=list)


@dataclass
class _Word:
    text: str
    key: tuple[int, int, int]
    x0: float
    x1: float
    top: float
    bottom: float


def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in WINDOWS_CANDIDATES:
            if candidate.is_file():
                return str(candidate)
    return None


@lru_cache
def _languages(tesseract: str) -> frozenset[str]:
    result = run_process([tesseract, "--list-langs"], timeout=30)
    output = (result.stdout or b"").decode("utf-8", errors="replace") + (result.stderr or b"").decode(
        "utf-8", errors="replace"
    )
    return frozenset(line.strip() for line in output.splitlines() if line.strip() and " " not in line.strip())


def ocr_available() -> bool:
    tesseract = find_tesseract()
    return tesseract is not None and {"rus", "eng"} <= _languages(tesseract)


def ensure_available() -> None:
    """Проверить Tesseract до рендеринга страниц, чтобы сразу сообщить понятную ошибку."""
    _require_tesseract()


def _require_tesseract() -> str:
    tesseract = find_tesseract()
    if tesseract is None or "rus" not in _languages(tesseract):
        raise ToolMissingError(
            "Документ — скан без текста, а распознавание на сервере не настроено "
            "(нет Tesseract с русским языком)."
        )
    return tesseract


def render_page(pdf_path: Path, index: int) -> tuple[bytes, float, tuple[float, float]]:
    """PNG страницы в оттенках серого, коэффициент «пикселей на пункт» и размер страницы в пунктах."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[index]
        width, height = page.get_size()
        scale = DPI / 72
        if max(width, height) * scale > MAX_SIDE_PX:
            scale = MAX_SIDE_PX / max(width, height)
        bitmap = page.render(scale=scale, grayscale=True)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.close()
        return buffer.getvalue(), scale, (float(width), float(height))
    finally:
        document.close()


def recognize_png(png: bytes, scale: float, page_number: int, page_size: tuple[float, float]) -> OcrPage:
    tesseract = _require_tesseract()
    image = Image.open(io.BytesIO(png)).convert("L")
    horizontal, vertical = find_rules(image, scale)
    tables = find_tables(horizontal, vertical, scale)
    if not tables:
        return parse_tsv(_tesseract(tesseract, png, "3"), scale, page_number, page_size)
    cleaned = erase_rules(image, horizontal, vertical, tables, scale)
    texts, confidences = _read_cells(tesseract, cleaned, tables, scale)
    # Остальной текст страницы распознаётся без ячеек: иначе текст таблицы попал бы в разбор дважды.
    words, page_confidences = _words(
        _tesseract(tesseract, _png(_blank_cells(cleaned, tables, scale)), "3"), scale
    )
    return OcrPage(
        lines=_lines(words, page_number, page_size),
        mean_confidence=_mean(confidences + page_confidences),
        tables=[_table(index, cells, texts) for index, cells in enumerate(tables)],
    )


def _tesseract(tesseract: str, png: bytes, psm: str) -> str:
    """TSV распознавания. psm 3 — страница с разметкой на блоки, psm 6 — один блок строк."""
    result = run_process(
        [tesseract, "stdin", "stdout", "-l", LANGUAGES, "--psm", psm, "tsv"],
        timeout=TESSERACT_TIMEOUT_SECONDS,
        input_bytes=png,
        # Несколько задач worker распознают параллельно: каждой — один поток OpenMP.
        env={"OMP_THREAD_LIMIT": "1"},
    )
    if result.returncode != 0:
        raise ConversionError("Не удалось распознать страницу документа.")
    return result.stdout.decode("utf-8", errors="replace")


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- линии и ячейки таблиц ---------------------------------------------------------------------------


def _runs(data: bytes, length: int, count: int, minimum: int) -> Iterator[Rule]:
    """Тёмные полосы не короче minimum в каждой из count строк длины length (0 — тёмный пиксель)."""
    pattern = re.compile(rb"\x00{%d,}" % minimum)
    for pos in range(count):
        base = pos * length
        for match in pattern.finditer(data, base, base + length):
            yield Rule(pos, match.start() - base, match.end() - base)


def find_rules(image: Image.Image, scale: float) -> tuple[list[Rule], list[Rule]]:
    """Горизонтальные и вертикальные линии на картинке страницы (оттенки серого)."""
    width, height = image.size
    mask = image.point(lambda value: 0 if value < RULE_DARK else 255)
    horizontal = list(_runs(mask.tobytes(), width, height, max(1, round(H_RULE_MIN_PT * scale))))
    if not horizontal:
        return [], []
    columns = mask.transpose(Image.Transpose.TRANSPOSE).tobytes()
    long_enough = round(V_RULE_MIN_PT * scale)
    touch = max(1, round(RULE_TOUCH_PT * scale))

    def touches(y: int, x: int) -> bool:
        return any(
            abs(rule.pos - y) <= touch and rule.start - touch <= x < rule.end + touch for rule in horizontal
        )

    vertical = [
        rule
        for rule in _runs(columns, height, width, max(1, round(V_DIVIDER_MIN_PT * scale)))
        if rule.end - rule.start >= long_enough
        or (touches(rule.start, rule.pos) and touches(rule.end - 1, rule.pos))
    ]
    return horizontal, vertical


def _edge(orientation: str, rule: Rule, scale: float) -> dict[str, Any]:
    pos, start, end = rule.pos / scale, rule.start / scale, rule.end / scale
    if orientation == "h":
        return {
            "orientation": "h",
            "x0": start,
            "x1": end,
            "top": pos,
            "bottom": pos,
            "doctop": pos,
            "width": end - start,
            "height": 0.0,
        }
    return {
        "orientation": "v",
        "x0": pos,
        "x1": pos,
        "top": start,
        "bottom": end,
        "doctop": start,
        "width": 0.0,
        "height": end - start,
    }


def find_tables(horizontal: list[Rule], vertical: list[Rule], scale: float) -> list[list[Bbox]]:
    """Таблицы — группы ячеек в пунктах PDF, сверху вниз. Ячейка — замкнутый прямоугольник из линий."""
    if not horizontal or not vertical:
        return []
    edges = [_edge("h", rule, scale) for rule in horizontal] + [_edge("v", rule, scale) for rule in vertical]
    tolerance = TABLE_TOLERANCE_PT
    edges = merge_edges(edges, tolerance, tolerance, tolerance, tolerance)
    edges = [edge for edge in edges if max(edge["width"], edge["height"]) >= EDGE_MIN_PT]
    cells = intersections_to_cells(edges_to_intersections(edges, tolerance, tolerance))
    return cells_to_tables(cells)


def erase_rules(
    image: Image.Image, horizontal: list[Rule], vertical: list[Rule], tables: list[list[Bbox]], scale: float
) -> Image.Image:
    """Копия картинки, где линии внутри таблиц закрашены белым. Линии вне таблиц не трогаются."""
    width, height = image.size
    margin = TABLE_TOLERANCE_PT * scale
    halo = max(1, round(RULE_HALO_PT * scale))
    boxes = [
        (
            min(c[0] for c in cells) * scale - margin,
            min(c[1] for c in cells) * scale - margin,
            max(c[2] for c in cells) * scale + margin,
            max(c[3] for c in cells) * scale + margin,
        )
        for cells in tables
    ]
    pixels = bytearray(image.tobytes())
    for rule in horizontal:
        if any(
            top <= rule.pos <= bottom and rule.start < x1 and rule.end > x0 for x0, top, x1, bottom in boxes
        ):
            for y in range(max(0, rule.pos - halo), min(height, rule.pos + halo + 1)):
                start, end = y * width + rule.start, y * width + rule.end
                if y == rule.pos:
                    pixels[start:end] = b"\xff" * (end - start)
                else:
                    pixels[start:end] = bytes(pixels[start:end]).translate(_LIGHT_TO_WHITE)
    for rule in vertical:
        if any(
            x0 <= rule.pos <= x1 and rule.start < bottom and rule.end > top for x0, top, x1, bottom in boxes
        ):
            for x in range(max(0, rule.pos - halo), min(width, rule.pos + halo + 1)):
                for y in range(rule.start, rule.end):
                    index = y * width + x
                    pixels[index] = 255 if x == rule.pos else _LIGHT_TO_WHITE[pixels[index]]
    return Image.frombytes("L", image.size, bytes(pixels))


# --- ячейки таблиц --------------------------------------------------------------------------------


def _blank_cells(image: Image.Image, tables: list[list[Bbox]], scale: float) -> Image.Image:
    blank = image.copy()
    draw = ImageDraw.Draw(blank)
    for cells in tables:
        for x0, top, x1, bottom in cells:
            draw.rectangle((x0 * scale, top * scale, x1 * scale, bottom * scale), fill=255)
    return blank


def _read_cells(
    tesseract: str, image: Image.Image, tables: list[list[Bbox]], scale: float
) -> tuple[dict[tuple[int, Bbox], str], list[float]]:
    """Текст ячеек: вырезки ячеек складываются столбиком и распознаются одним вызовом Tesseract (psm 6).

    Страницу целиком Tesseract размечает на строки, и ячейки с подписью в две строки рядом с однострочными
    он склеивает в одну строку двойной высоты. Сложенные столбиком вырезки — просто строки текста одна
    под другой. Строка распознавания возвращается в свою ячейку по высоте на общей картинке.
    """
    inset = max(1, round(CELL_INSET_PT * scale))
    crops: list[tuple[tuple[int, Bbox], Image.Image]] = []
    for index, cells in enumerate(tables):
        for cell in cells:
            box = (
                round(cell[0] * scale) + inset,
                round(cell[1] * scale) + inset,
                round(cell[2] * scale) - inset,
                round(cell[3] * scale) - inset,
            )
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                continue
            crop = image.crop(box)
            # Ни одного тёмного пикселя — ячейка пустая, распознавать нечего.
            if crop.getextrema()[0] < RULE_DARK:
                crops.append(((index, cell), crop))
    texts: dict[tuple[int, Bbox], str] = {}
    confidences: list[float] = []
    for chunk in _chunks(crops):
        width = max(crop.width for _, crop in chunk) + 2 * CELL_PAD_PX
        height = CELL_PAD_PX + sum(crop.height + CELL_GAP_PX for _, crop in chunk)
        canvas = Image.new("L", (width, height), 255)
        spans: list[tuple[tuple[int, Bbox], int, int]] = []
        y = CELL_PAD_PX
        for place, crop in chunk:
            canvas.paste(crop, (CELL_PAD_PX, y))
            spans.append((place, y - CELL_GAP_PX // 2, y + crop.height + CELL_GAP_PX // 2))
            y += crop.height + CELL_GAP_PX
        words, chunk_confidences = _words(_tesseract(tesseract, _png(canvas), "6"), 1.0)
        confidences += chunk_confidences
        grouped: dict[tuple[int, Bbox], list[_Word]] = {}
        for word in words:
            middle = (word.top + word.bottom) / 2
            place = next((place for place, top, bottom in spans if top <= middle < bottom), None)
            if place is not None:
                grouped.setdefault(place, []).append(word)
        texts.update((place, _cell_text(group)) for place, group in grouped.items())
    return texts, confidences


def _chunks(
    crops: list[tuple[tuple[int, Bbox], Image.Image]],
) -> Iterator[list[tuple[tuple[int, Bbox], Image.Image]]]:
    """Вырезки по картинкам высотой не больше MAX_STACK_PX."""
    chunk: list[tuple[tuple[int, Bbox], Image.Image]] = []
    height = CELL_PAD_PX
    for item in crops:
        extra = item[1].height + CELL_GAP_PX
        if chunk and height + extra > MAX_STACK_PX:
            yield chunk
            chunk, height = [], CELL_PAD_PX
        chunk.append(item)
        height += extra
    if chunk:
        yield chunk


def _table(index: int, cells: list[Bbox], texts: dict[tuple[int, Bbox], str]) -> OcrTable:
    rows = [
        [None if cell is None else texts.get((index, cell), "") for cell in row.cells]
        for row in Table(None, cells).rows
    ]
    return OcrTable(top=min(cell[1] for cell in cells), rows=rows)


def _cell_text(words: list[_Word]) -> str:
    """Текст ячейки по строкам: слово попадает в строку, если его середина по высоте внутри этой строки."""
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        middle = (word.top + word.bottom) / 2
        line = lines[-1] if lines else None
        if line and min(item.top for item in line) <= middle <= max(item.bottom for item in line):
            line.append(word)
        else:
            lines.append([word])
    return "\n".join(" ".join(word.text for word in sorted(line, key=lambda item: item.x0)) for line in lines)


# --- TSV Tesseract -----------------------------------------------------------------------------------


def parse_tsv(tsv: str, scale: float, page_number: int, page_size: tuple[float, float]) -> OcrPage:
    """Слова из TSV Tesseract → строки с координатами в пунктах PDF."""
    words, confidences = _words(tsv, scale)
    return OcrPage(lines=_lines(words, page_number, page_size), mean_confidence=_mean(confidences))


def _words(tsv: str, scale: float) -> tuple[list[_Word], list[float]]:
    """Слова (уровень 5 TSV) с координатами, делёнными на scale, и уверенность распознавания каждого."""
    words: list[_Word] = []
    confidences: list[float] = []
    for row in tsv.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or cells[0] != "5":
            continue
        text = cells[11].strip()
        if not text:
            continue
        try:
            key = (int(cells[2]), int(cells[3]), int(cells[4]))
            left, top, width, height = (int(cells[i]) for i in range(6, 10))
            conf = float(cells[10])
        except ValueError:
            continue
        if conf >= 0:
            confidences.append(conf)
        words.append(
            _Word(text, key, left / scale, (left + width) / scale, top / scale, (top + height) / scale)
        )
    return words, confidences


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _lines(words: list[_Word], page_number: int, page_size: tuple[float, float]) -> list[Line]:
    grouped: dict[tuple[int, int, int], list[_Word]] = {}
    for word in words:
        grouped.setdefault(word.key, []).append(word)
    page_width, page_height = page_size
    # Порядок блоков Tesseract — порядок чтения: колонки (например, реквизиты сторон) не перемешиваются.
    return [
        Line(
            text=" ".join(word.text for word in group),
            x0=min(word.x0 for word in group),
            x1=max(word.x1 for word in group),
            top=min(word.top for word in group),
            bottom=max(word.bottom for word in group),
            page=page_number,
            page_width=page_width,
            page_height=page_height,
            block=key[0],
            par=key[1],
        )
        for key, group in sorted(grouped.items())
    ]
