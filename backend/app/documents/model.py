"""Структуры, которые возвращает чтение документа."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class FileKind(StrEnum):
    DOCX = "docx"
    DOC = "doc"
    RTF = "rtf"
    PDF = "pdf"


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CELL = "cell"


@dataclass
class RawParagraph:
    """Абзац до разметки на пункты: так его отдают читатели docx, PDF и OCR."""

    text: str
    # Метка автонумерации Word («9.2.», «а)») или маркер списка; для PDF и сканов — None.
    label: str | None = None
    is_bullet: bool = False
    # Признаки заголовка: стиль «Heading N» или уровень структуры из Word.
    heading_level: int | None = None
    bold: bool = False
    # Ячейка таблицы: (номер таблицы, строка, колонка), считая с нуля.
    table: tuple[int, int, int] | None = None
    page: int | None = None


@dataclass
class Block:
    id: str
    kind: BlockKind
    # Текст так, как его видит читатель: вместе с меткой автонумерации.
    text: str
    # Номер пункта, к которому относится блок («9.2»); None — до первого пункта (преамбула).
    clause: str | None = None
    # Заголовок раздела, в котором стоит блок.
    section: str | None = None
    label: str | None = None
    table: tuple[int, int, int] | None = None
    page: int | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["table"] = list(self.table) if self.table else None
        return data


@dataclass
class ExtractedDocument:
    kind: FileKind
    blocks: list[Block]
    ocr_used: bool = False
    page_count: int | None = None
    # Предупреждения для менеджера и для анализа: скрытый текст, правки, низкое качество распознавания.
    warnings: list[str] = field(default_factory=list)
