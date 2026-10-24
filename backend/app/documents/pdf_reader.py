"""Чтение PDF: текстовый слой через pdfplumber, страницы без текста — через распознавание (ocr.py).

Таблицы с линиями находит pdfplumber, на скане — ocr.py по линиям картинки. Их ячейки становятся
отдельными блоками, а текст вне таблиц собирается в абзацы (text_lines.py). Колонтитулы убираются.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from app.documents import ocr
from app.documents.errors import DamagedFileError, DocumentError, ProtectedFileError
from app.documents.model import RawParagraph
from app.documents.text_lines import Line, layout_stats, lines_to_paragraphs, remove_headers_and_footers

MAX_PAGES = 300
MAX_OCR_PAGES = 60
# Как часто сообщать о ходе чтения текста: сообщение заодно продлевает признак жизни задачи в очереди.
PROGRESS_EVERY_PAGES = 10
# Страница считается сканом, если текста на ней почти нет, а картинка есть.
MIN_TEXT_CHARS = 15

OCR_WARNING = "Текст распознан со скана: возможны ошибки в отдельных словах. Сверьте цитаты с оригиналом."
LOW_QUALITY_WARNING = (
    "Качество распознавания низкое: проверьте результат особенно внимательно или загрузите docx."
)
PROTECTED_MESSAGE = "PDF защищён паролем. Снимите пароль и загрузите файл снова."
DAMAGED_MESSAGE = "Файл повреждён: не удаётся открыть PDF."

Progress = Callable[[str], None]


@dataclass
class _Table:
    page: int
    top: float
    rows: list[list[str | None]]
    # Номер таблицы в документе: раздаётся по порядку страниц, когда прочитаны и распознаны все страницы.
    index: int = 0


@dataclass
class _Page:
    number: int
    lines: list[Line]
    tables: list[_Table]


@dataclass
class PdfContent:
    paragraphs: list[RawParagraph]
    page_count: int
    ocr_used: bool
    warnings: list[str] = field(default_factory=list)


def _open_error(exc: Exception) -> DocumentError:
    original = exc.args[0] if isinstance(exc, PdfminerException) and exc.args else exc
    if isinstance(original, PDFPasswordIncorrect):
        return ProtectedFileError(PROTECTED_MESSAGE)
    return DamagedFileError(DAMAGED_MESSAGE)


def read_pdf(path: Path, progress: Progress | None = None) -> PdfContent:
    pages: list[_Page] = []
    scan_pages: list[int] = []
    try:
        pdf = pdfplumber.open(path)
    except (PdfminerException, MalformedPDFException, PDFPasswordIncorrect, ValueError, KeyError) as exc:
        raise _open_error(exc) from exc
    with pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise DamagedFileError("В PDF нет страниц.")
        if page_count > MAX_PAGES:
            raise DocumentError(f"В документе {page_count} страниц — это слишком много для договора.")
        for number, page in enumerate(pdf.pages, start=1):
            if progress and number % PROGRESS_EVERY_PAGES == 0:
                progress(f"Чтение страницы {number} из {page_count}")
            try:
                page_data, chars = _read_page(page, number)
                has_images = bool(page.images)
            except (PdfminerException, MalformedPDFException, ValueError, KeyError, TypeError) as exc:
                raise DamagedFileError(f"Файл повреждён: не удаётся прочитать страницу {number}.") from exc
            if chars < MIN_TEXT_CHARS and has_images:
                scan_pages.append(number)
                pages.append(_Page(number=number, lines=[], tables=[]))
                continue
            pages.append(page_data)

    warnings: list[str] = []
    if scan_pages:
        if len(scan_pages) > MAX_OCR_PAGES:
            raise DocumentError(
                f"В скане {len(scan_pages)} страниц, распознать можно не больше {MAX_OCR_PAGES}. "
                "Загрузите договор в docx или PDF с текстом."
            )
        ocr.ensure_available()
        confidences = []
        for done, number in enumerate(scan_pages, start=1):
            if progress:
                progress(f"Распознавание страницы {done} из {len(scan_pages)}")
            png, scale, size = ocr.render_page(path, number - 1)
            recognized = ocr.recognize_png(png, scale, number, size)
            pages[number - 1].lines = recognized.lines
            pages[number - 1].tables = [
                _Table(page=number, top=table.top, rows=table.rows) for table in recognized.tables
            ]
            if recognized.mean_confidence is not None:
                confidences.append(recognized.mean_confidence)
        warnings.append(OCR_WARNING)
        if confidences and sum(confidences) / len(confidences) < ocr.LOW_CONFIDENCE:
            warnings.append(LOW_QUALITY_WARNING)

    for index, table in enumerate(table for page in pages for table in page.tables):
        table.index = index
    all_lines = [line for page in pages for line in page.lines]
    kept = {id(line) for line in remove_headers_and_footers(all_lines, page_count)}
    for page in pages:
        page.lines = [line for line in page.lines if id(line) in kept]
    stats = layout_stats([line for page in pages for line in page.lines]) if kept else None
    return PdfContent(
        paragraphs=_assemble(pages, stats),
        page_count=page_count,
        ocr_used=bool(scan_pages),
        warnings=warnings,
    )


def _read_page(page, number: int) -> tuple[_Page, int]:
    width, height = float(page.width), float(page.height)
    tables: list[_Table] = []
    boxes: list[tuple[float, float, float, float]] = []
    chars = 0
    for table in page.find_tables():
        rows = table.extract()
        tables.append(_Table(page=number, top=float(table.bbox[1]), rows=rows))
        chars += sum(len((cell or "").strip()) for row in rows for cell in row)
        boxes.append(tuple(float(value) for value in table.bbox))

    def outside_tables(obj: dict) -> bool:
        # Символ исключается, только если его центр внутри таблицы. outside_bbox выбросил бы и строку,
        # которая лишь касается рамки таблицы нижними выносными элементами букв.
        if obj.get("object_type") != "char":
            return True
        cx = (obj["x0"] + obj["x1"]) / 2
        cy = (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

    text_page = page.filter(outside_tables) if boxes else page
    lines = []
    for item in text_page.extract_text_lines(strip=True, return_chars=False):
        text = item.get("text", "")
        if not text.strip():
            continue
        chars += len(text.strip())
        lines.append(
            Line(
                text=text,
                x0=float(item["x0"]),
                x1=float(item["x1"]),
                top=float(item["top"]),
                bottom=float(item["bottom"]),
                page=number,
                page_width=width,
                page_height=height,
            )
        )
    tables.sort(key=lambda table: table.top)
    return _Page(number=number, lines=lines, tables=tables), chars


def _assemble(pages: list[_Page], stats) -> list[RawParagraph]:
    """Абзацы и ячейки таблиц в порядке документа.

    Строки внутри страницы идут в том порядке, в каком их отдал читатель: у PDF с текстом — сверху вниз,
    у распознанной страницы — по блокам Tesseract. Таблица встаёт перед первой строкой ниже неё.
    """
    result: list[RawParagraph] = []
    buffer: list[Line] = []

    def flush() -> None:
        if buffer and stats is not None:
            for text, page in lines_to_paragraphs(buffer, stats):
                result.append(RawParagraph(text=text, page=page))
        buffer.clear()

    def emit_table(table: _Table) -> None:
        flush()
        for row_index, row in enumerate(table.rows):
            for col_index, cell in enumerate(row):
                text = "\n".join(" ".join(part.split()) for part in (cell or "").splitlines() if part.strip())
                if text:
                    result.append(
                        RawParagraph(text=text, table=(table.index, row_index, col_index), page=table.page)
                    )

    for page in pages:
        pending = list(page.tables)
        for line in page.lines:
            while pending and pending[0].top <= line.top:
                emit_table(pending.pop(0))
            buffer.append(line)
        for table in pending:
            emit_table(table)
    flush()
    return result
