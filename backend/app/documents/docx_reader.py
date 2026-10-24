"""Чтение .docx напрямую из XML (zipfile + lxml).

python-docx здесь не подходит: он не восстанавливает автонумерацию и падает на нестандартных
значениях атрибутов (в реальных договорах поля страницы бывают заданы дробными числами).

Что берётся: абзацы и таблицы основного текста в порядке документа, текст надписей (text box),
сноски. Что не берётся: колонтитулы, комментарии, удалённый в режиме правок и скрытый текст.
Исправления в режиме рецензирования читаются как принятые.
"""

import posixpath
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from app.documents.errors import DamagedFileError
from app.documents.model import RawParagraph
from app.documents.numbering import (
    LEVELS,
    MAX_LEVEL_TEXT,
    AbstractNum,
    LevelDef,
    LevelOverride,
    NumberingDefinitions,
    NumberingState,
    NumInstance,
    clamp_start,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
OFFICE_DOCUMENT_TYPES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
)


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


W_P, W_TBL, W_TR, W_TC, W_R, W_T = w("p"), w("tbl"), w("tr"), w("tc"), w("r"), w("t")
W_SDT, W_SDT_CONTENT, W_CUSTOM_XML = w("sdt"), w("sdtContent"), w("customXml")
W_INS, W_DEL, W_MOVE_TO, W_MOVE_FROM = w("ins"), w("del"), w("moveTo"), w("moveFrom")
W_HYPERLINK, W_SMART_TAG, W_FLD_SIMPLE = w("hyperlink"), w("smartTag"), w("fldSimple")
W_TXBX_CONTENT = w("txbxContent")
W_VAL = w("val")
MC_ALTERNATE, MC_CHOICE, MC_FALLBACK = (
    f"{{{MC_NS}}}AlternateContent",
    f"{{{MC_NS}}}Choice",
    f"{{{MC_NS}}}Fallback",
)

# Безопасный разбор XML из загруженного файла: без внешних сущностей и сети (защита от XXE).
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
# Одна XML-часть больше этого — не договор: дерево lxml занимает в памяти в разы больше самого XML.
# Общий размер архива ограничен при определении типа (detect.py, MAX_ZIP_UNCOMPRESSED).
MAX_XML_PART_BYTES = 64 * 1024 * 1024

HIDDEN_WARNING = (
    "В документе есть скрытый (невидимый) текст, в анализ он не включён. Проверьте документ: "
    "скрытым текстом иногда прячут условия."
)
TRACKED_WARNING = (
    "В документе есть исправления в режиме рецензирования. Анализ сделан по тексту с принятыми исправлениями."
)
COMMENTS_WARNING = "В документе есть примечания (комментарии), в анализ они не входят."


class StrictOpenXmlError(Exception):
    """Документ в формате Strict Open XML: его нужно пересохранить через LibreOffice."""


def _on(element: etree._Element | None) -> bool:
    """Значение переключателя Word: <w:b/> — включено, <w:b w:val="0"/> — выключено."""
    if element is None:
        return False
    return element.get(W_VAL, "true").lower() not in {"0", "false", "off", "none"}


def _int(value: str | None, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, OverflowError):
        # «nan» и «1e999» (бесконечность) — не число.
        return default


@dataclass
class StyleInfo:
    style_id: str
    name: str = ""
    based_on: str | None = None
    num_id: str | None = None
    ilvl: int | None = None
    outline_level: int | None = None
    bold: bool = False
    caps: bool = False


@dataclass
class Flags:
    hidden: bool = False
    tracked: bool = False
    comments: bool = False


@dataclass
class DocxContent:
    paragraphs: list[RawParagraph]
    warnings: list[str] = field(default_factory=list)


class _Package:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.names = set(archive.namelist())

    def xml(self, name: str) -> etree._Element | None:
        if name not in self.names:
            return None
        # Размер из заголовка архива: zipfile не прочитает больше заявленного (иначе — ошибка CRC).
        if self.archive.getinfo(name).file_size > MAX_XML_PART_BYTES:
            raise DamagedFileError("Файл повреждён или слишком велик для документа Word.")
        try:
            data = self.archive.read(name)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            # Испорченные сжатые данные или контрольная сумма части архива.
            raise DamagedFileError("Файл повреждён: не удаётся распаковать документ.") from exc
        try:
            return etree.fromstring(data, PARSER)
        except etree.XMLSyntaxError as exc:
            raise DamagedFileError("Файл повреждён: не удаётся прочитать структуру документа.") from exc

    def main_part(self) -> str:
        rels = self.xml("_rels/.rels")
        if rels is not None:
            for rel in rels.iter(f"{{{REL_NS}}}Relationship"):
                if rel.get("Type") in OFFICE_DOCUMENT_TYPES:
                    return rel.get("Target", "word/document.xml").lstrip("/")
        return "word/document.xml"

    def related_parts(self, part: str) -> dict[str, str]:
        """Тип связи (последняя часть URI) → имя части. Например, numbering → word/numbering.xml."""
        directory, filename = posixpath.split(part)
        rels = self.xml(posixpath.join(directory, "_rels", f"{filename}.rels"))
        result: dict[str, str] = {}
        if rels is None:
            return result
        for rel in rels.iter(f"{{{REL_NS}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            kind = rel.get("Type", "").rsplit("/", 1)[-1]
            target = rel.get("Target", "")
            path = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join(directory, target))
            )
            result.setdefault(kind, path)
        return result


def _parse_styles(root: etree._Element | None) -> tuple[dict[str, StyleInfo], str | None, dict[str, str]]:
    styles: dict[str, StyleInfo] = {}
    default_paragraph: str | None = None
    numbering_styles: dict[str, str] = {}
    if root is None:
        return styles, default_paragraph, numbering_styles
    for style in root.iter(w("style")):
        style_id = style.get(w("styleId"))
        if not style_id:
            continue
        style_type = style.get(w("type"))
        ppr = style.find(w("pPr"))
        num_pr = ppr.find(w("numPr")) if ppr is not None else None
        num_id_el = num_pr.find(w("numId")) if num_pr is not None else None
        num_id = num_id_el.get(W_VAL) if num_id_el is not None else None
        if style_type == "numbering":
            if num_id:
                numbering_styles[style_id] = num_id
            continue
        if style_type != "paragraph":
            continue
        rpr = style.find(w("rPr"))
        name_el = style.find(w("name"))
        based_el = style.find(w("basedOn"))
        ilvl_el = num_pr.find(w("ilvl")) if num_pr is not None else None
        outline_el = ppr.find(w("outlineLvl")) if ppr is not None else None
        styles[style_id] = StyleInfo(
            style_id=style_id,
            name=(name_el.get(W_VAL, "") if name_el is not None else ""),
            based_on=based_el.get(W_VAL) if based_el is not None else None,
            num_id=num_id,
            ilvl=_int(ilvl_el.get(W_VAL)) if ilvl_el is not None else None,
            outline_level=_int(outline_el.get(W_VAL)) if outline_el is not None else None,
            bold=_on(rpr.find(w("b"))) if rpr is not None else False,
            caps=_on(rpr.find(w("caps"))) if rpr is not None else False,
        )
        default_attr = style.get(w("default"))
        if default_attr is not None and default_attr.lower() in {"1", "true", "on"}:
            default_paragraph = style_id
    return styles, default_paragraph, numbering_styles


def _parse_level(lvl: etree._Element) -> LevelDef:
    def val(tag: str) -> str | None:
        element = lvl.find(w(tag))
        return element.get(W_VAL) if element is not None else None

    start = _int(val("start"))
    return LevelDef(
        start=clamp_start(start) if start is not None else 1,
        fmt=val("numFmt") or "decimal",
        text=(val("lvlText") or "")[:MAX_LEVEL_TEXT],
        restart=_int(val("lvlRestart")),
        is_lgl=lvl.find(w("isLgl")) is not None and _on(lvl.find(w("isLgl"))),
        pstyle=val("pStyle"),
    )


def _parse_numbering(root: etree._Element | None, numbering_styles: dict[str, str]) -> NumberingDefinitions:
    definitions = NumberingDefinitions(style_nums=dict(numbering_styles))
    if root is None:
        return definitions
    for abstract in root.iter(w("abstractNum")):
        abstract_id = abstract.get(w("abstractNumId"))
        if abstract_id is None:
            continue
        levels = {}
        for lvl in abstract.findall(w("lvl")):
            level = _int(lvl.get(w("ilvl")), 0) or 0
            # В Word уровней девять (0–8); остальные — ошибка файла.
            if 0 <= level < LEVELS:
                levels[level] = _parse_level(lvl)
        link = abstract.find(w("numStyleLink"))
        definitions.abstracts[abstract_id] = AbstractNum(
            levels=levels, num_style_link=link.get(W_VAL) if link is not None else None
        )
    for num in root.iter(w("num")):
        num_id = num.get(w("numId"))
        abstract_ref = num.find(w("abstractNumId"))
        if num_id is None or abstract_ref is None:
            continue
        overrides = {}
        for override in num.findall(w("lvlOverride")):
            level = _int(override.get(w("ilvl")), 0) or 0
            if not 0 <= level < LEVELS:
                continue
            start_el = override.find(w("startOverride"))
            lvl_el = override.find(w("lvl"))
            start = _int(start_el.get(W_VAL)) if start_el is not None else None
            overrides[level] = LevelOverride(
                start=clamp_start(start) if start is not None else None,
                level=_parse_level(lvl_el) if lvl_el is not None else None,
            )
        definitions.nums[num_id] = NumInstance(abstract_id=abstract_ref.get(W_VAL, ""), overrides=overrides)
    return definitions


class _Reader:
    def __init__(
        self,
        styles: dict[str, StyleInfo],
        default_style: str | None,
        numbering: NumberingDefinitions,
    ) -> None:
        self.styles = styles
        self.default_style = default_style
        self.state = NumberingState(numbering)
        self.flags = Flags()
        self.paragraphs: list[RawParagraph] = []
        self._tables = 0

    # --- стили -------------------------------------------------------------------------
    def _style_chain(self, style_id: str | None) -> Iterator[StyleInfo]:
        seen = set()
        while style_id and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            yield style
            style_id = style.based_on

    def _style_numbering(self, style_id: str | None) -> tuple[str | None, int | None]:
        for style in self._style_chain(style_id):
            if style.num_id is not None:
                return style.num_id, style.ilvl
        return None, None

    def _heading_level(self, style_id: str | None, direct_outline: int | None) -> int | None:
        if direct_outline is not None:
            return direct_outline if direct_outline < 9 else None
        for style in self._style_chain(style_id):
            name = style.name.strip().lower()
            for prefix in ("heading ", "заголовок "):
                if name.startswith(prefix) and name[len(prefix) :].strip().isdigit():
                    return int(name[len(prefix) :].strip()) - 1
            if style.outline_level is not None:
                return style.outline_level if style.outline_level < 9 else None
        return None

    def _style_flag(self, style_id: str | None, attr: str) -> bool:
        return any(getattr(style, attr) for style in self._style_chain(style_id))

    # --- обход -------------------------------------------------------------------------
    @staticmethod
    def _block_children(container: etree._Element) -> Iterator[etree._Element]:
        """Абзацы и таблицы контейнера; элементы управления содержимым (w:sdt) раскрываются."""
        for child in container:
            if child.tag in (W_P, W_TBL):
                yield child
            elif child.tag == W_SDT:
                content = child.find(W_SDT_CONTENT)
                if content is not None:
                    yield from _Reader._block_children(content)
            elif child.tag in (W_CUSTOM_XML, W_INS, W_MOVE_TO):
                yield from _Reader._block_children(child)

    @staticmethod
    def _rows(table: etree._Element) -> Iterator[etree._Element]:
        for child in table:
            if child.tag == W_TR:
                yield child
            elif child.tag in (W_SDT, W_CUSTOM_XML):
                content = child.find(W_SDT_CONTENT) if child.tag == W_SDT else child
                if content is not None:
                    yield from _Reader._rows(content)

    @staticmethod
    def _cells(row: etree._Element) -> Iterator[etree._Element]:
        for child in row:
            if child.tag == W_TC:
                yield child
            elif child.tag in (W_SDT, W_CUSTOM_XML):
                content = child.find(W_SDT_CONTENT) if child.tag == W_SDT else child
                if content is not None:
                    yield from _Reader._cells(content)

    def read_container(self, container: etree._Element, table: tuple[int, int, int] | None = None) -> None:
        for element in self._block_children(container):
            if element.tag == W_P:
                self._paragraph(element, table)
            else:
                self._table(element)

    def _table(self, table: etree._Element) -> None:
        index = self._tables
        self._tables += 1
        for row_index, row in enumerate(self._rows(table)):
            for col_index, cell in enumerate(self._cells(row)):
                self.read_container(cell, (index, row_index, col_index))

    def _paragraph(self, p: etree._Element, table: tuple[int, int, int] | None) -> None:
        ppr = p.find(w("pPr"))
        style_el = ppr.find(w("pStyle")) if ppr is not None else None
        style_id = style_el.get(W_VAL) if style_el is not None else self.default_style
        num_pr = ppr.find(w("numPr")) if ppr is not None else None
        outline_el = ppr.find(w("outlineLvl")) if ppr is not None else None

        label_text: str | None = None
        is_bullet = False
        num_id, ilvl = self._resolve_numbering(num_pr, style_id)
        if num_id is not None:
            label = self.state.next_label(num_id, ilvl)
            if label is not None and label.text.strip():
                label_text = label.text.strip()
                is_bullet = label.is_bullet

        parts: list[str] = []
        floating: list[etree._Element] = []
        stats = {"chars": 0, "bold": 0}
        caps_style = self._style_flag(style_id, "caps")
        bold_style = self._style_flag(style_id, "bold")
        self._collect(p, parts, floating, stats, caps_style, bold_style)

        text = _normalize("".join(parts))
        if text or label_text:
            self.paragraphs.append(
                RawParagraph(
                    text=text,
                    label=label_text,
                    is_bullet=is_bullet,
                    heading_level=self._heading_level(
                        style_id, _int(outline_el.get(W_VAL)) if outline_el is not None else None
                    ),
                    bold=stats["chars"] > 0 and stats["bold"] >= 0.9 * stats["chars"],
                    table=table,
                )
            )
        # Текст надписей идёт сразу после абзаца, к которому они привязаны.
        for content in floating:
            self.read_container(content, table)

    def _resolve_numbering(
        self, num_pr: etree._Element | None, style_id: str | None
    ) -> tuple[str | None, int]:
        direct_num = direct_ilvl = None
        if num_pr is not None:
            num_el = num_pr.find(w("numId"))
            ilvl_el = num_pr.find(w("ilvl"))
            direct_num = num_el.get(W_VAL) if num_el is not None else None
            direct_ilvl = _int(ilvl_el.get(W_VAL)) if ilvl_el is not None else None
        style_num, style_ilvl = self._style_numbering(style_id)
        num_id = direct_num if direct_num is not None else style_num
        if num_id is None or num_id == "0":
            return None, 0
        if direct_ilvl is not None:
            return num_id, direct_ilvl
        if style_ilvl is not None:
            return num_id, style_ilvl
        linked = self.state.level_for_style(num_id, style_id)
        return num_id, linked if linked is not None else 0

    def _collect(
        self,
        element: etree._Element,
        parts: list[str],
        floating: list[etree._Element],
        stats: dict[str, int],
        caps_style: bool,
        bold_style: bool,
    ) -> None:
        for child in element:
            tag = child.tag
            if tag == W_R:
                self._run(child, parts, floating, stats, caps_style, bold_style)
            elif tag in (W_DEL, W_MOVE_FROM):
                self.flags.tracked = True
            elif tag in (W_INS, W_MOVE_TO):
                self.flags.tracked = True
                self._collect(child, parts, floating, stats, caps_style, bold_style)
            elif tag in (W_HYPERLINK, W_SMART_TAG, W_CUSTOM_XML, W_FLD_SIMPLE, w("dir"), w("bdo")):
                self._collect(child, parts, floating, stats, caps_style, bold_style)
            elif tag == W_SDT:
                content = child.find(W_SDT_CONTENT)
                if content is not None:
                    self._collect(content, parts, floating, stats, caps_style, bold_style)
            elif tag == MC_ALTERNATE:
                chosen = child.find(MC_CHOICE)
                if chosen is None:
                    chosen = child.find(MC_FALLBACK)
                if chosen is not None:
                    self._collect(chosen, parts, floating, stats, caps_style, bold_style)
            elif tag in (w("commentRangeStart"), w("commentReference")):
                self.flags.comments = True

    def _run(
        self,
        run: etree._Element,
        parts: list[str],
        floating: list[etree._Element],
        stats: dict[str, int],
        caps_style: bool,
        bold_style: bool,
    ) -> None:
        rpr = run.find(w("rPr"))
        if rpr is not None and _on(rpr.find(w("vanish"))):
            if any((t.text or "").strip() for t in run.iter(W_T)):
                self.flags.hidden = True
            return
        bold_el = rpr.find(w("b")) if rpr is not None else None
        bold = _on(bold_el) if bold_el is not None else bold_style
        caps_el = rpr.find(w("caps")) if rpr is not None else None
        caps = _on(caps_el) if caps_el is not None else caps_style
        for child in run:
            tag = child.tag
            if tag == W_T:
                text = child.text or ""
                if caps:
                    text = text.upper()
                parts.append(text)
                visible = len(text.strip())
                stats["chars"] += visible
                if bold:
                    stats["bold"] += visible
            elif tag in (w("tab"), w("ptab")):
                parts.append("\t")
            elif tag in (w("br"), w("cr")):
                parts.append("\n")
            elif tag == w("noBreakHyphen"):
                parts.append("-")
            elif tag == w("commentReference"):
                self.flags.comments = True
            elif tag in (w("drawing"), w("pict"), MC_ALTERNATE):
                self._find_text_boxes(child, floating)

    @staticmethod
    def _find_text_boxes(element: etree._Element, floating: list[etree._Element]) -> None:
        if element.tag == MC_ALTERNATE:
            chosen = element.find(MC_CHOICE)
            if chosen is None:
                chosen = element.find(MC_FALLBACK)
            if chosen is not None:
                for child in chosen:
                    _Reader._find_text_boxes(child, floating)
            return
        for content in element.iter(W_TXBX_CONTENT):
            # Надпись внутри другой надписи найдётся, когда будет читаться внешняя: здесь её пропускаем.
            nested = False
            for parent in content.iterancestors():
                if parent is element:
                    break
                if parent.tag == W_TXBX_CONTENT:
                    nested = True
                    break
            if not nested:
                floating.append(content)


def _normalize(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\t", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _notes(package: _Package, part: str | None, tag: str, reader: _Reader, title: str) -> None:
    if part is None:
        return
    root = package.xml(part)
    if root is None:
        return
    for note in root.iter(w(tag)):
        if note.get(w("type")) in {"separator", "continuationSeparator", "continuationNotice"}:
            continue
        start = len(reader.paragraphs)
        reader.read_container(note)
        for paragraph in reader.paragraphs[start:]:
            paragraph.text = f"[{title} {note.get(w('id'), '')}] {paragraph.text}".strip()


def read_docx(path: Path) -> DocxContent:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DamagedFileError("Файл повреждён: не удаётся открыть архив документа.") from exc
    with archive:
        package = _Package(archive)
        main = package.main_part()
        document = package.xml(main)
        if document is None:
            raise DamagedFileError("Файл повреждён: в нём нет основного текста документа.")
        if document.tag.startswith(f"{{{STRICT_W_NS}}}"):
            raise StrictOpenXmlError
        related = package.related_parts(main)
        styles, default_style, numbering_styles = _parse_styles(package.xml(related.get("styles", "")))
        numbering = _parse_numbering(package.xml(related.get("numbering", "")), numbering_styles)

        reader = _Reader(styles, default_style, numbering)
        body = document.find(w("body"))
        if body is None:
            raise DamagedFileError("Файл повреждён: в нём нет основного текста документа.")
        reader.read_container(body)
        _notes(package, related.get("footnotes"), "footnote", reader, "сноска")
        _notes(package, related.get("endnotes"), "endnote", reader, "концевая сноска")
        if related.get("comments") in package.names:
            reader.flags.comments = True

    warnings = []
    if reader.flags.hidden:
        warnings.append(HIDDEN_WARNING)
    if reader.flags.tracked:
        warnings.append(TRACKED_WARNING)
    if reader.flags.comments:
        warnings.append(COMMENTS_WARNING)
    return DocxContent(paragraphs=reader.paragraphs, warnings=warnings)
