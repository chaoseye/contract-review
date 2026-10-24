"""Чтение договора целиком: тип файла → текст → блоки с номерами пунктов."""

from collections.abc import Callable
from pathlib import Path

from app.documents.convert import convert
from app.documents.detect import detect_kind
from app.documents.docx_reader import StrictOpenXmlError, read_docx
from app.documents.errors import DocumentError
from app.documents.model import ExtractedDocument, FileKind, RawParagraph
from app.documents.pdf_reader import read_pdf
from app.documents.segment import segment

Progress = Callable[[str], None]
EMPTY_MESSAGE = "В документе не найден текст."


def extract_document(path: Path, work_dir: Path, progress: Progress | None = None) -> ExtractedDocument:
    """Прочитать договор. work_dir — папка для промежуточных файлов (результат конвертации)."""
    kind = detect_kind(path)
    warnings: list[str] = []
    page_count: int | None = None
    ocr_used = False

    if kind == FileKind.PDF:
        content = read_pdf(path, progress)
        paragraphs: list[RawParagraph] = content.paragraphs
        page_count = content.page_count
        ocr_used = content.ocr_used
        warnings.extend(content.warnings)
    else:
        paragraphs, docx_warnings = _read_word(path, kind, work_dir, progress)
        warnings.extend(docx_warnings)

    blocks, segment_warnings = segment(paragraphs)
    if not blocks:
        raise DocumentError(EMPTY_MESSAGE)
    warnings.extend(segment_warnings)
    return ExtractedDocument(
        kind=kind, blocks=blocks, ocr_used=ocr_used, page_count=page_count, warnings=warnings
    )


def _read_word(
    path: Path, kind: FileKind, work_dir: Path, progress: Progress | None
) -> tuple[list[RawParagraph], list[str]]:
    source = path
    if kind in (FileKind.DOC, FileKind.RTF):
        if progress:
            progress("Перевод файла в docx")
        source = convert(path, "docx", work_dir)
    try:
        content = read_docx(source)
    except StrictOpenXmlError:
        # Strict Open XML: LibreOffice пересохраняет его в обычный docx.
        if progress:
            progress("Пересохранение документа в обычный docx")
        strict_copy = work_dir / "strict-source.docx"
        work_dir.mkdir(parents=True, exist_ok=True)
        strict_copy.write_bytes(path.read_bytes())
        content = read_docx(convert(strict_copy, "docx", work_dir / "converted"))
    return content.paragraphs, content.warnings
