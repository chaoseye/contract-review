"""Определение типа файла по содержимому."""

import zipfile
from pathlib import Path

import pytest

from app.documents.detect import OLE_SIGNATURE, detect_kind
from app.documents.errors import DamagedFileError, ProtectedFileError, UnsupportedFileError
from tests.docx_builder import make_docx, p


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_docx(tmp_path: Path) -> None:
    assert detect_kind(make_docx(tmp_path / "x.bin", p("текст"))) == "docx"


def test_pdf_even_with_wrong_extension(tmp_path: Path) -> None:
    assert detect_kind(write(tmp_path / "x.docx", b"%PDF-1.7\n...")) == "pdf"


def test_rtf(tmp_path: Path) -> None:
    assert detect_kind(write(tmp_path / "x.doc", b"{\\rtf1\\ansi \\u1090? text}")) == "rtf"


def test_old_word(tmp_path: Path) -> None:
    data = OLE_SIGNATURE + b"\x00" * 100 + "WordDocument".encode("utf-16-le") + b"\x00" * 100
    assert detect_kind(write(tmp_path / "x.doc", data)) == "doc"


def test_password_protected_docx(tmp_path: Path) -> None:
    data = OLE_SIGNATURE + b"\x00" * 100 + "EncryptedPackage".encode("utf-16-le")
    with pytest.raises(ProtectedFileError):
        detect_kind(write(tmp_path / "x.docx", data))


def test_excel_in_ole_is_unsupported(tmp_path: Path) -> None:
    data = OLE_SIGNATURE + b"\x00" * 100 + "Workbook".encode("utf-16-le")
    with pytest.raises(UnsupportedFileError):
        detect_kind(write(tmp_path / "x.xls", data))


def test_xlsx_is_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        )
    with pytest.raises(UnsupportedFileError):
        detect_kind(path)


def test_broken_zip_is_damaged(tmp_path: Path) -> None:
    with pytest.raises(DamagedFileError):
        detect_kind(write(tmp_path / "x.docx", b"PK\x03\x04" + b"\x00" * 50))


def test_empty_file(tmp_path: Path) -> None:
    with pytest.raises(DamagedFileError):
        detect_kind(write(tmp_path / "x.pdf", b""))


def test_plain_text_is_unsupported(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFileError):
        detect_kind(write(tmp_path / "x.txt", "Договор поставки".encode()))
