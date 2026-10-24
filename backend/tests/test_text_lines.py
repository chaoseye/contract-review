"""Сборка абзацев из строк PDF и скана, удаление колонтитулов."""

from app.documents.text_lines import Line, layout_stats, lines_to_paragraphs, remove_headers_and_footers

PAGE_W, PAGE_H = 595.0, 842.0
LEFT, RIGHT = 70.0, 525.0


def line(text: str, top: float, page: int = 1, x0: float = LEFT, x1: float = RIGHT, **kwargs) -> Line:
    return Line(
        text=text,
        x0=x0,
        x1=x1,
        top=top,
        bottom=top + 12,
        page=page,
        page_width=PAGE_W,
        page_height=PAGE_H,
        **kwargs,
    )


def paragraphs(lines: list[Line]) -> list[str]:
    return [text for text, _page in lines_to_paragraphs(lines, layout_stats(lines))]


def test_clause_start_opens_paragraph_and_wrapped_lines_join() -> None:
    lines = [
        line("1.1. Поставщик обязуется передать в собственность", 100),
        line("Покупателя товар, а Покупатель обязуется принять", 114),
        line("и оплатить его.", 128, x1=200),
        line("1.2. Наименование товара указывается в Спецификации.", 142),
    ]
    assert paragraphs(lines) == [
        "1.1. Поставщик обязуется передать в собственность Покупателя товар, а Покупатель обязуется принять "
        "и оплатить его.",
        "1.2. Наименование товара указывается в Спецификации.",
    ]


def test_hyphenated_word_is_joined() -> None:
    lines = [line("Индивидуальный пред-", 100), line("приниматель Иванов И.И.", 114, x1=300)]
    assert paragraphs(lines) == ["Индивидуальный предприниматель Иванов И.И."]


def test_real_hyphen_before_capital_is_kept() -> None:
    lines = [line("город Санкт-", 100), line("Петербург и далее", 114, x1=300)]
    assert paragraphs(lines) == ["город Санкт- Петербург и далее"]


def test_large_gap_starts_new_paragraph() -> None:
    lines = [
        line("Первый абзац без точки в конце строки и без номера", 100),
        line("продолжение первого абзаца", 114, x1=300),
        line("Второй абзац после большого отступа", 160),
    ]
    assert paragraphs(lines) == [
        "Первый абзац без точки в конце строки и без номера продолжение первого абзаца",
        "Второй абзац после большого отступа",
    ]


def test_heading_lines_stand_alone() -> None:
    lines = [
        line("2. ЦЕНА И ПОРЯДОК РАСЧЕТОВ", 100, x0=200, x1=400),
        line("2.1. Цена Договора составляет", 114),
    ]
    assert paragraphs(lines) == ["2. ЦЕНА И ПОРЯДОК РАСЧЕТОВ", "2.1. Цена Договора составляет"]


def test_paragraph_continues_across_pages() -> None:
    lines = [
        line("3.1. Поставка осуществляется силами", 780),
        line("и за счёт Поставщика.", 60, page=2, x1=250),
    ]
    assert paragraphs(lines) == ["3.1. Поставка осуществляется силами и за счёт Поставщика."]


def test_ocr_blocks_split_paragraphs() -> None:
    lines = [
        line("Покупатель", 100, x1=250, block=1, par=1),
        line("ИНН 1234567890", 114, x1=250, block=1, par=1),
        line("Поставщик", 100, x0=300, block=2, par=1),
    ]
    assert paragraphs(lines) == ["Покупатель ИНН 1234567890", "Поставщик"]


def test_page_numbers_and_repeated_headers_are_removed() -> None:
    lines = []
    for page in range(1, 5):
        lines.append(line("ООО «Пример» — договор поставки", 20, page=page))
        lines.append(line(f"{page}.1. Текст страницы {page}.", 100, page=page))
        lines.append(line(f"Страница | {page}", 815, page=page, x0=280, x1=320))
    kept = remove_headers_and_footers(lines, page_count=4)
    assert [x.text for x in kept] == [f"{page}.1. Текст страницы {page}." for page in range(1, 5)]


def test_single_page_footer_number_removed_but_text_kept() -> None:
    lines = [line("Важное условие внизу страницы", 790), line("- 1 -", 815, x0=290, x1=305)]
    assert [x.text for x in remove_headers_and_footers(lines, page_count=1)] == [
        "Важное условие внизу страницы"
    ]
