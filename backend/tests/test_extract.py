"""Чтение договора целиком: docx, doc и rtf через LibreOffice, пустые документы."""

from pathlib import Path

import pytest

from app.documents.convert import convert, find_soffice
from app.documents.errors import DocumentError, ToolMissingError
from app.documents.extract import EMPTY_MESSAGE, extract_document
from tests.docx_builder import abstract, level, make_docx, num, numbering, p

needs_soffice = pytest.mark.skipif(find_soffice() is None, reason="нет LibreOffice (проверяется в Docker)")


def numbered_docx(path: Path) -> Path:
    body = (
        p("ДОГОВОР ПОСТАВКИ")
        + p("ПРЕДМЕТ ДОГОВОРА", num="1", ilvl=0)
        + p("Поставщик обязуется передать товар.", num="1", ilvl=1)
        + p("Покупатель обязуется оплатить товар.", num="1", ilvl=1)
    )
    xml = numbering(abstract("1", level(0) + level(1)), num("1", "1"))
    return make_docx(path, body, numbering_xml=xml)


def rtf_text(text: str) -> str:
    escaped = "".join(ch if ord(ch) < 128 else f"\\u{ord(ch)}?" for ch in text)
    return (
        "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\\f0 " + escaped.replace("\n", "\\par\n") + "}"
    )


def test_docx_end_to_end(tmp_path: Path) -> None:
    document = extract_document(numbered_docx(tmp_path / "a.docx"), tmp_path / "work")
    assert document.kind == "docx"
    assert [(b.text, b.clause) for b in document.blocks] == [
        ("ДОГОВОР ПОСТАВКИ", None),
        ("1. ПРЕДМЕТ ДОГОВОРА", "1"),
        ("1.1. Поставщик обязуется передать товар.", "1.1"),
        ("1.2. Покупатель обязуется оплатить товар.", "1.2"),
    ]


def test_empty_document(tmp_path: Path) -> None:
    with pytest.raises(DocumentError, match=EMPTY_MESSAGE):
        extract_document(make_docx(tmp_path / "a.docx", p("")), tmp_path / "work")


@pytest.mark.skipif(find_soffice() is not None, reason="LibreOffice установлен")
def test_doc_without_libreoffice_gives_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "a.doc"
    path.write_text(rtf_text("1.1. Текст"), encoding="ascii")
    with pytest.raises(ToolMissingError, match="LibreOffice"):
        extract_document(path, tmp_path / "work")


@needs_soffice
def test_doc_keeps_auto_numbering(tmp_path: Path) -> None:
    source = numbered_docx(tmp_path / "source.docx")
    doc = convert(source, "doc", tmp_path / "doc")
    document = extract_document(doc, tmp_path / "work")
    assert document.kind == "doc"
    assert [b.clause for b in document.blocks] == [None, "1", "1.1", "1.2"]
    assert document.blocks[2].text == "1.1. Поставщик обязуется передать товар."


@needs_soffice
def test_rtf_disguised_as_doc(tmp_path: Path) -> None:
    path = tmp_path / "contract.doc"
    path.write_text(
        rtf_text("1.1. Поставщик обязуется передать товар.\n1.2. Покупатель оплачивает."), encoding="ascii"
    )
    document = extract_document(path, tmp_path / "work")
    assert document.kind == "rtf"
    assert [b.clause for b in document.blocks] == ["1.1", "1.2"]
    assert document.blocks[0].text == "1.1. Поставщик обязуется передать товар."
