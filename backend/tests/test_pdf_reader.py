"""Чтение PDF: текстовый слой, таблицы, колонтитулы, сканы, пароли, повреждения."""

from pathlib import Path

import pytest

from app.documents.errors import DamagedFileError, ProtectedFileError, ToolMissingError
from app.documents.extract import extract_document
from app.documents.ocr import ocr_available
from app.documents.pdf_reader import OCR_WARNING, read_pdf
from tests.pdf_builder import cyrillic_font, make_scan_pdf, make_text_pdf

pytestmark = pytest.mark.skipif(cyrillic_font() is None, reason="нет шрифта с кириллицей для тестовых PDF")

CONTRACT_PAGES: list[list] = [
    [
        "ДОГОВОР ПОСТАВКИ № 7",
        "г. Москва",
        "1. ПРЕДМЕТ ДОГОВОРА",
        "1.1. Поставщик обязуется передать товар, а Покупатель",
        "обязуется принять и оплатить его.",
        "1.2. Количество товара указано в таблице:",
        [["№", "Наименование", "Кол-во"], ["1.", "Насос", "3"]],
        "2. ЦЕНА",
        "2.1. Цена составляет 100 000 рублей.",
    ],
    [
        "2.2. Оплата производится в течение 5 рабочих дней с даты под-",
        "писания УПД.",
        "3. ОТВЕТСТВЕННОСТЬ",
        "3.1. Неустойка составляет 0,1% в день.",
    ],
    ["4. ЗАКЛЮЧИТЕЛЬНЫЕ ПОЛОЖЕНИЯ", "4.1. Договор вступает в силу с даты подписания."],
]


def clause_sequence(blocks) -> list[str]:
    result: list[str] = []
    for block in blocks:
        if block.clause and (not result or result[-1] != block.clause):
            result.append(block.clause)
    return result


def test_text_pdf_is_split_into_clauses(tmp_path: Path) -> None:
    path = make_text_pdf(tmp_path / "contract.pdf", CONTRACT_PAGES)
    document = extract_document(path, tmp_path / "work")
    assert document.kind == "pdf"
    assert document.page_count == 3
    assert not document.ocr_used
    assert clause_sequence(document.blocks) == ["1", "1.1", "1.2", "2", "2.1", "2.2", "3", "3.1", "4", "4.1"]

    texts = [block.text for block in document.blocks]
    assert "1.1. Поставщик обязуется передать товар, а Покупатель обязуется принять и оплатить его." in texts
    assert "2.2. Оплата производится в течение 5 рабочих дней с даты подписания УПД." in texts
    assert not any("Страница" in text for text in texts)
    assert document.blocks[0].kind == "heading"
    assert document.blocks[0].clause is None


def test_pdf_table_cells_are_blocks_and_row_numbers_do_not_change_clause(tmp_path: Path) -> None:
    path = make_text_pdf(tmp_path / "contract.pdf", CONTRACT_PAGES)
    document = extract_document(path, tmp_path / "work")
    cells = [block for block in document.blocks if block.kind == "cell"]
    assert [(cell.text, cell.table) for cell in cells] == [
        ("№", (0, 0, 0)),
        ("Наименование", (0, 0, 1)),
        ("Кол-во", (0, 0, 2)),
        ("1.", (0, 1, 0)),
        ("Насос", (0, 1, 1)),
        ("3", (0, 1, 2)),
    ]
    assert {cell.clause for cell in cells} == {"1.2"}
    assert all(cell.page == 1 for cell in cells)


def test_password_protected_pdf(tmp_path: Path) -> None:
    path = make_text_pdf(tmp_path / "secret.pdf", CONTRACT_PAGES[:1], password="secret")
    with pytest.raises(ProtectedFileError):
        read_pdf(path)


def test_damaged_pdf(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(DamagedFileError):
        read_pdf(path)


@pytest.mark.skipif(ocr_available(), reason="Tesseract установлен — проверяется распознавание, а не ошибка")
def test_scan_without_tesseract_gives_clear_error(tmp_path: Path) -> None:
    path = make_scan_pdf(tmp_path / "scan.pdf", ["1.1. Поставщик обязуется передать товар."])
    with pytest.raises(ToolMissingError, match="Tesseract"):
        extract_document(path, tmp_path / "work")


@pytest.mark.skipif(not ocr_available(), reason="нет Tesseract с русским языком (проверяется в Docker)")
def test_scan_is_recognized(tmp_path: Path) -> None:
    path = make_scan_pdf(
        tmp_path / "scan.pdf",
        [
            "ДОГОВОР ПОСТАВКИ",
            "1. ПРЕДМЕТ ДОГОВОРА",
            "1.1. Поставщик обязуется передать товар.",
            "1.2. Покупатель обязуется оплатить товар.",
        ],
    )
    progress: list[str] = []
    document = extract_document(path, tmp_path / "work", progress=progress.append)
    assert document.ocr_used
    assert OCR_WARNING in document.warnings
    assert progress == ["Распознавание страницы 1 из 1"]
    assert clause_sequence(document.blocks) == ["1", "1.1", "1.2"]
    joined = " ".join(block.text for block in document.blocks)
    assert "Поставщик обязуется передать товар" in joined
    assert "Покупатель обязуется оплатить товар" in joined
