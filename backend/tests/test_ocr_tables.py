"""Таблицы на скане: линии на картинке, ячейки, стирание линий, текст ячеек.

Поиск линий и ячеек проверяется на нарисованной картинке без Tesseract. Распознавание целиком — в Docker.
"""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.documents import ocr
from app.documents.extract import extract_document
from app.documents.ocr import ocr_available
from tests.pdf_builder import cyrillic_font, make_scan_pdf

SCALE = 300 / 72


def drawn_page() -> Image.Image:
    """A4 при 300 dpi: таблица 3 × 2 с перегородкой в нижней строке, подчёркивание и «буква» вне таблицы."""
    image = Image.new("L", (2480, 3508), 255)
    draw = ImageDraw.Draw(image)
    for y in (400, 600, 800, 870):
        draw.line((300, y, 2100, y), fill=0, width=4)
    for x in (300, 1200, 2100):
        draw.line((x, 400, x, 870), fill=0, width=4)
    # Нижняя строка высотой 70 px делится ещё одной короткой перегородкой: короче V_RULE_MIN_PT,
    # но оба её конца упираются в горизонтальные линии.
    draw.line((1650, 800, 1650, 870), fill=0, width=4)
    # Подчёркивание без вертикальных линий — это не таблица, и его не стираем.
    draw.line((300, 1200, 1500, 1200), fill=0, width=4)
    # Вертикальный штрих высотой как у буквы, ни во что не упирается: не линия.
    draw.line((700, 1300, 700, 1345), fill=0, width=5)
    return image


def test_table_cells_are_found_from_drawn_rules() -> None:
    horizontal, vertical = ocr.find_rules(drawn_page(), SCALE)
    tables = ocr.find_tables(horizontal, vertical, SCALE)
    assert len(tables) == 1
    rows = [[cell is not None for cell in row.cells] for row in ocr.Table(None, tables[0]).rows]
    # Строки: две ячейки, две ячейки, три ячейки (нижняя разделена перегородкой).
    assert [sum(row) for row in rows] == [2, 2, 3]
    top = min(cell[1] for cell in tables[0])
    assert top == pytest.approx(400 / SCALE, abs=1.5)
    # Штрих «буквы» в линии не попал.
    assert not any(rule.pos in range(695, 706) and rule.start >= 1290 for rule in vertical)


def test_only_table_rules_are_erased() -> None:
    image = drawn_page()
    horizontal, vertical = ocr.find_rules(image, SCALE)
    tables = ocr.find_tables(horizontal, vertical, SCALE)
    erased = ocr.erase_rules(image, horizontal, vertical, tables, SCALE)
    # Линии таблицы и края линий стёрты.
    for x, y in ((800, 400), (800, 402), (800, 870), (300, 500), (1200, 700), (1650, 835)):
        assert erased.getpixel((x, y)) == 255, (x, y)
    # Подчёркивание и «буква» вне таблицы остались.
    assert erased.getpixel((900, 1200)) == 0
    assert erased.getpixel((700, 1320)) == 0


def test_page_without_tables_has_no_rules_to_erase() -> None:
    image = Image.new("L", (2480, 3508), 255)
    ImageDraw.Draw(image).line((300, 1200, 1500, 1200), fill=0, width=4)
    horizontal, vertical = ocr.find_rules(image, SCALE)
    assert horizontal and not vertical
    assert ocr.find_tables(horizontal, vertical, SCALE) == []


def test_cell_text_lines_follow_height_not_tesseract_keys() -> None:
    words = [
        ocr._Word("измер.", (1, 1, 2), 10, 60, 40, 55),
        ocr._Word("Ед.", (1, 1, 1), 20, 50, 10, 25),
        # Tesseract поделил строку на два блока: в ячейке это одна строка.
        ocr._Word("за", (2, 1, 1), 60, 80, 11, 24),
    ]
    assert ocr._cell_text(words) == "Ед. за\nизмер."


@pytest.mark.skipif(not ocr_available(), reason="нет Tesseract с русским языком (проверяется в Docker)")
@pytest.mark.skipif(cyrillic_font() is None, reason="нет шрифта с кириллицей для тестового скана")
def test_scan_table_cells_are_blocks(tmp_path: Path) -> None:
    path = make_scan_pdf(
        tmp_path / "scan.pdf",
        [
            "1. ПРЕДМЕТ ДОГОВОРА",
            "1.1. Поставщик передает товар:",
            [
                ["Наименование", "Кол-во", "Ед.\nизмер."],
                ["Насос центробежный", "3", "шт."],
                ["Страна: Россия", "", ""],
            ],
            "1.2. Покупатель оплачивает товар.",
        ],
        font_px=46,
    )
    document = extract_document(path, tmp_path / "work")
    cells = {block.table: block.text for block in document.blocks if block.kind == "cell"}
    assert cells[(0, 0, 0)] == "Наименование"
    assert cells[(0, 0, 2)] == "Ед.\nизмер."
    assert cells[(0, 1, 0)] == "Насос центробежный"
    # Короткие значения в ячейках Tesseract на целой странице пропускал.
    assert cells[(0, 1, 1)] == "3"
    assert cells[(0, 2, 0)] == "Страна: Россия"
    # Таблица — между пунктами 1.1 и 1.2, текст таблицы не повторяется в абзацах.
    kinds = [(block.kind, block.clause) for block in document.blocks]
    first_cell = kinds.index(("cell", "1.1"))
    assert ("paragraph", "1.1") in kinds[:first_cell]
    assert kinds[-1] == ("paragraph", "1.2")
    paragraphs = " ".join(block.text for block in document.blocks if block.kind != "cell")
    assert "Насос" not in paragraphs
