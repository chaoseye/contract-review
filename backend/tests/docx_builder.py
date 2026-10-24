"""Сборка минимальных .docx для тестов: только нужные части, XML пишется вручную.

Так тесты точно управляют автонумерацией, стилями и разметкой и не зависят от Word или LibreOffice.
"""

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = (
    f'xmlns:w="{W_NS}" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)


def run(text: str, *, bold: bool = False, hidden: bool = False, caps: bool = False) -> str:
    props = ("<w:b/>" if bold else "") + ("<w:vanish/>" if hidden else "") + ("<w:caps/>" if caps else "")
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def p(
    text: str = "",
    *,
    style: str | None = None,
    num: str | None = None,
    ilvl: int | None = None,
    runs: str | None = None,
    bold: bool = False,
) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num is not None or ilvl is not None:
        ppr += "<w:numPr>"
        if ilvl is not None:
            ppr += f'<w:ilvl w:val="{ilvl}"/>'
        if num is not None:
            ppr += f'<w:numId w:val="{num}"/>'
        ppr += "</w:numPr>"
    body = runs if runs is not None else (run(text, bold=bold) if text else "")
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{body}</w:p>"


def table(rows: list[list[str]]) -> str:
    """rows: строки → ячейки, каждая ячейка — XML абзацев (например, p("текст"))."""
    xml = "<w:tbl>"
    for row in rows:
        xml += "<w:tr>" + "".join(f"<w:tc>{cell}</w:tc>" for cell in row) + "</w:tr>"
    return xml + "</w:tbl>"


def level(ilvl: int, fmt: str = "decimal", text: str | None = None, start: int = 1, extra: str = "") -> str:
    lvl_text = text if text is not None else "".join(f"%{i + 1}." for i in range(ilvl + 1))
    return (
        f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{escape(lvl_text)}"/>{extra}</w:lvl>'
    )


def abstract(abstract_id: str, levels: str, extra: str = "") -> str:
    return f'<w:abstractNum w:abstractNumId="{abstract_id}">{extra}{levels}</w:abstractNum>'


def num(num_id: str, abstract_id: str, overrides: str = "") -> str:
    return f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/>{overrides}</w:num>'


def numbering(*parts: str) -> str:
    return f"<w:numbering {NS}>{''.join(parts)}</w:numbering>"


def styles(*parts: str) -> str:
    return f"<w:styles {NS}>{''.join(parts)}</w:styles>"


def paragraph_style(
    style_id: str,
    name: str,
    *,
    num_id: str | None = None,
    ilvl: int | None = None,
    extra_ppr: str = "",
    rpr: str = "",
) -> str:
    numpr = ""
    if num_id is not None:
        numpr = "<w:numPr>" + (f'<w:ilvl w:val="{ilvl}"/>' if ilvl is not None else "")
        numpr += f'<w:numId w:val="{num_id}"/></w:numPr>'
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
        f"<w:pPr>{numpr}{extra_ppr}</w:pPr>{f'<w:rPr>{rpr}</w:rPr>' if rpr else ''}</w:style>"
    )


def make_docx(
    path: Path,
    body: str,
    *,
    numbering_xml: str | None = None,
    styles_xml: str | None = None,
    footnotes_xml: str | None = None,
    document_prefix: str = "",
) -> Path:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    rels = []
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    if styles_xml:
        rels.append(f'<Relationship Id="rIdS" Type="{base}/styles" Target="styles.xml"/>')
    if numbering_xml:
        rels.append(f'<Relationship Id="rIdN" Type="{base}/numbering" Target="numbering.xml"/>')
    if footnotes_xml:
        rels.append(f'<Relationship Id="rIdF" Type="{base}/footnotes" Target="footnotes.xml"/>')
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>{document_prefix}'
        f"<w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("word/document.xml", document)
        if styles_xml:
            archive.writestr("word/styles.xml", styles_xml)
        if numbering_xml:
            archive.writestr("word/numbering.xml", numbering_xml)
        if footnotes_xml:
            archive.writestr("word/footnotes.xml", footnotes_xml)
    return path
