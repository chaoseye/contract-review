"""Тестовые PDF: с текстом (reportlab) и «сканы» (картинка, нарисованная Pillow).

Нужен шрифт с кириллицей. В образе Docker это DejaVu и Liberation, на Windows — Arial или Times New Roman.
Если шрифта нет, тесты с PDF пропускаются.
"""

from pathlib import Path

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/times.ttf",
)

PAGE_W, PAGE_H = 595.0, 842.0
LEFT = 70.0
FONT_SIZE = 11
LEADING = 14


def cyrillic_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def make_text_pdf(
    path: Path,
    pages: list[list[str | list[list[str]]]],
    *,
    footer: bool = True,
    password: str | None = None,
) -> Path:
    """pages: страницы → элементы; строка — строка текста, список строк ячеек — таблица с линиями."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = cyrillic_font()
    if font is None:
        raise RuntimeError("нет шрифта с кириллицей")
    pdfmetrics.registerFont(TTFont("TestCyr", str(font)))
    pdf = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H), encrypt=password)
    for number, items in enumerate(pages, start=1):
        y = PAGE_H - 70
        for item in items:
            if isinstance(item, str):
                pdf.setFont("TestCyr", FONT_SIZE)
                pdf.drawString(LEFT, y, item)
                y -= LEADING
                continue
            # Таблица: сетка линий и текст в ячейках.
            columns = len(item[0])
            col_width = (PAGE_W - 2 * LEFT) / columns
            row_height = 20
            top = y + 12
            for row_index, row in enumerate(item):
                for col_index, cell in enumerate(row):
                    pdf.setFont("TestCyr", FONT_SIZE)
                    pdf.drawString(LEFT + col_index * col_width + 4, top - row_height * row_index - 14, cell)
            bottom = top - row_height * len(item)
            for row_index in range(len(item) + 1):
                pdf.line(LEFT, top - row_height * row_index, PAGE_W - LEFT, top - row_height * row_index)
            for col_index in range(columns + 1):
                pdf.line(LEFT + col_width * col_index, top, LEFT + col_width * col_index, bottom)
            y = bottom - LEADING - 6
        if footer:
            pdf.setFont("TestCyr", 9)
            pdf.drawString(PAGE_W / 2 - 25, 30, f"Страница | {number}")
        pdf.showPage()
    pdf.save()
    return path


def make_scan_pdf(path: Path, lines: list[str | list[list[str]]], font_px: int = 58) -> Path:
    """Страница-картинка 300 dpi без текстового слоя, как у отсканированного документа.

    lines: строка — строка текста; список строк ячеек — таблица с линиями 4 px. В ячейке может быть
    несколько строк через \n.
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = cyrillic_font()
    if font_path is None:
        raise RuntimeError("нет шрифта с кириллицей")
    width, height = int(PAGE_W / 72 * 300), int(PAGE_H / 72 * 300)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), font_px)
    left, right = 290, width - 290
    leading = int(font_px * 1.6)
    y = 300
    for item in lines:
        if isinstance(item, str):
            draw.text((left, y), item, fill=0, font=font)
            y += leading
            continue
        col_width = (right - left) // len(item[0])
        top = y
        for row in item:
            row_height = max(len(cell.split("\n")) for cell in row) * int(font_px * 1.3) + 40
            for col_index, cell in enumerate(row):
                for line_index, part in enumerate(cell.split("\n")):
                    position = (left + col_index * col_width + 25, y + 20 + line_index * int(font_px * 1.3))
                    draw.text(position, part, fill=0, font=font)
            draw.line((left, y, right, y), fill=0, width=4)
            y += row_height
        draw.line((left, y, right, y), fill=0, width=4)
        for col_index in range(len(item[0]) + 1):
            x = min(left + col_index * col_width, right)
            draw.line((x, top, x, y), fill=0, width=4)
        y += leading
    image.save(path, "PDF", resolution=300)
    return path
