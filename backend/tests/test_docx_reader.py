"""Чтение docx: таблицы, правки, скрытый текст, надписи, сноски, безопасность разбора."""

import zipfile
from pathlib import Path

import pytest

from app.documents.docx_reader import COMMENTS_WARNING, HIDDEN_WARNING, TRACKED_WARNING, read_docx
from app.documents.errors import DamagedFileError
from app.documents.segment import segment
from tests.docx_builder import NS, make_docx, p, paragraph_style, run, styles, table


def texts(path: Path) -> list[str]:
    return [paragraph.text for paragraph in read_docx(path).paragraphs]


def test_tables_keep_order_and_coordinates(tmp_path: Path) -> None:
    body = p("До таблицы") + table([[p("A1"), p("B1")], [p("A2"), p("B2") + p("B2 ещё")]]) + p("После")
    content = read_docx(make_docx(tmp_path / "a.docx", body))
    assert [(x.text, x.table) for x in content.paragraphs] == [
        ("До таблицы", None),
        ("A1", (0, 0, 0)),
        ("B1", (0, 0, 1)),
        ("A2", (0, 1, 0)),
        ("B2", (0, 1, 1)),
        ("B2 ещё", (0, 1, 1)),
        ("После", None),
    ]


def test_nested_table_gets_its_own_index(tmp_path: Path) -> None:
    body = table([[p("внешняя") + table([[p("внутренняя")]])]]) + table([[p("вторая")]])
    content = read_docx(make_docx(tmp_path / "a.docx", body))
    assert [(x.text, x.table) for x in content.paragraphs] == [
        ("внешняя", (0, 0, 0)),
        ("внутренняя", (1, 0, 0)),
        ("вторая", (2, 0, 0)),
    ]


def test_content_controls_and_hyperlinks_are_read(tmp_path: Path) -> None:
    body = (
        f"<w:sdt><w:sdtContent>{p('В элементе управления')}</w:sdtContent></w:sdt>"
        f"<w:p><w:hyperlink>{run('ссылка')}</w:hyperlink>"
        f"<w:sdt><w:sdtContent>{run(' и поле')}</w:sdtContent></w:sdt></w:p>"
    )
    assert texts(make_docx(tmp_path / "a.docx", body)) == ["В элементе управления", "ссылка и поле"]


def test_tracked_changes_read_as_accepted(tmp_path: Path) -> None:
    body = (
        "<w:p>"
        + run("Неустойка ")
        + f"<w:del><w:r><w:delText>0,5%</w:delText></w:r></w:del><w:ins>{run('0,1%')}</w:ins>"
        + run(" в день")
        + "</w:p>"
    )
    content = read_docx(make_docx(tmp_path / "a.docx", body))
    assert [x.text for x in content.paragraphs] == ["Неустойка 0,1% в день"]
    assert TRACKED_WARNING in content.warnings


def test_hidden_text_is_excluded_with_warning(tmp_path: Path) -> None:
    body = "<w:p>" + run("Видимое. ") + run("Скрытое условие.", hidden=True) + "</w:p>"
    content = read_docx(make_docx(tmp_path / "a.docx", body))
    assert [x.text for x in content.paragraphs] == ["Видимое."]
    assert HIDDEN_WARNING in content.warnings


def test_field_codes_are_skipped_but_results_kept(tmp_path: Path) -> None:
    body = (
        "<w:p>"
        + run("Страница ")
        + '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + run("3")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        + "</w:p>"
    )
    assert texts(make_docx(tmp_path / "a.docx", body)) == ["Страница 3"]


def test_text_box_is_read_once_after_host_paragraph(tmp_path: Path) -> None:
    box = f"<w:txbxContent>{p('Текст в надписи')}</w:txbxContent>"
    drawing = (
        "<w:r><mc:AlternateContent>"
        f'<mc:Choice Requires="wps"><w:drawing><wp:anchor><a:graphic><a:graphicData><wps:wsp><wps:txbx>{box}'
        "</wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict><v:shape><v:textbox>{box}</v:textbox></v:shape></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r>"
    )
    body = "<w:p>" + run("Абзац с надписью") + drawing + "</w:p>" + p("Следующий")
    assert texts(make_docx(tmp_path / "a.docx", body)) == ["Абзац с надписью", "Текст в надписи", "Следующий"]


def test_footnotes_are_appended(tmp_path: Path) -> None:
    footnotes = (
        f"<w:footnotes {NS}>"
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        f'<w:footnote w:id="1">{p("Неустойка не начисляется в праздники.")}</w:footnote>'
        "</w:footnotes>"
    )
    body = "<w:p>" + run("Неустойка 0,1%") + '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
    content = read_docx(make_docx(tmp_path / "a.docx", body, footnotes_xml=footnotes))
    assert [x.text for x in content.paragraphs] == [
        "Неустойка 0,1%",
        "[сноска 1] Неустойка не начисляется в праздники.",
    ]


def test_comments_produce_warning(tmp_path: Path) -> None:
    body = "<w:p>" + '<w:commentRangeStart w:id="0"/>' + run("Текст") + '<w:commentRangeEnd w:id="0"/></w:p>'
    assert COMMENTS_WARNING in read_docx(make_docx(tmp_path / "a.docx", body)).warnings


def test_caps_bold_and_heading_styles(tmp_path: Path) -> None:
    style_xml = styles(
        paragraph_style("Title1", "heading 1"),
        paragraph_style("Caps", "Caps", rpr="<w:caps/><w:b/>"),
    )
    body = p("Договор поставки", style="Title1") + p("предмет договора", style="Caps") + p("обычный текст")
    content = read_docx(make_docx(tmp_path / "a.docx", body, styles_xml=style_xml))
    assert [(x.text, x.heading_level, x.bold) for x in content.paragraphs] == [
        ("Договор поставки", 0, False),
        ("ПРЕДМЕТ ДОГОВОРА", None, True),
        ("обычный текст", None, False),
    ]
    blocks, _ = segment(content.paragraphs)
    assert [b.kind for b in blocks] == ["heading", "heading", "paragraph"]
    assert blocks[2].section == "ПРЕДМЕТ ДОГОВОРА"


def test_whitespace_is_normalized(tmp_path: Path) -> None:
    body = "<w:p>" + run("1.1.  Текст") + "<w:r><w:tab/></w:r>" + run("после   табуляции") + "</w:p>"
    assert texts(make_docx(tmp_path / "a.docx", body)) == ["1.1. Текст после табуляции"]


def test_line_breaks_inside_paragraph_are_kept(tmp_path: Path) -> None:
    body = "<w:p>" + run("ИНН 1234567890") + "<w:r><w:br/></w:r>" + run("КПП 123456789") + "</w:p>"
    assert texts(make_docx(tmp_path / "a.docx", body)) == ["ИНН 1234567890\nКПП 123456789"]


def test_external_entities_are_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("СЕКРЕТНОЕ СОДЕРЖИМОЕ", encoding="utf-8")
    prefix = f'<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
    body = "<w:p><w:r><w:t>до &xxe; после</w:t></w:r></w:p>"
    path = make_docx(tmp_path / "a.docx", body, document_prefix=prefix)
    try:
        result = " ".join(texts(path))
    except DamagedFileError:
        return  # отказаться читать такой файл — тоже безопасно
    assert "СЕКРЕТНОЕ" not in result


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_broken_xml_is_reported_as_damaged(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "a.docx", p("текст"))
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("word/document.xml", "<w:document>не XML")
    with pytest.raises(DamagedFileError):
        read_docx(path)


def test_not_a_zip_is_reported_as_damaged(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK\x03\x04 broken")
    with pytest.raises(DamagedFileError):
        read_docx(path)
