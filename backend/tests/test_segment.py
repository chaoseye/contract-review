"""Разметка на пункты: номера, перечни, преамбула, таблицы, приложения."""

import pytest

from app.documents.model import RawParagraph
from app.documents.segment import (
    DUPLICATE_WARNING,
    is_heading,
    parse_clause_number,
    segment,
    starts_new_item,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9.2. В случае просрочки", "9.2"),
        ("9.2 В случае просрочки", "9.2"),
        ("9.2.1. Подпункт", "9.2.1"),
        ("1.1.Поставщик обязуется", "1.1"),
        ("9. ОТВЕТСТВЕННОСТЬ СТОРОН", "9"),
        ("12.3.1.Стороны договорились", "12.3.1"),
        ("Статья 5. Цена", "5"),
        ("Раздел IV. Приёмка", "4"),
        ("31 июля 2026 года", None),
        ("31.07.2026 г.", None),
        ("100 рублей", None),
        ("1) самовывозом", None),
        ("а) принять товар", None),
        ("0.5% от стоимости", None),
        ("п. 3.2 настоящего Договора", None),
        ("Поставщик обязуется", None),
        ("2026. Год заключения", None),
    ],
)
def test_parse_clause_number(text: str, expected: str | None) -> None:
    assert parse_clause_number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3.1. Срок", True),
        ("— самовывоз;", True),
        ("а) товар", True),
        ("1) товар", True),
        ("договора", False),
    ],
)
def test_starts_new_item(text: str, expected: bool) -> None:
    assert starts_new_item(text) is expected


def raw(text: str, **kwargs) -> RawParagraph:
    return RawParagraph(text=text, **kwargs)


# Вымышленные номер госконтракта (19 цифр) и идентификационный код закупки (36 цифр).
CONTRACT_19 = "0100000000026000001"
IKZ_36 = "261770000000077000100100010010000244"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1. ПРЕДМЕТ ДОГОВОРА", True),
        ("СТАТЬЯ 5", True),
        ("СПЕЦИФИКАЦИЯ", True),
        ("11. АДРЕСА И БАНКОВСКИЕ РЕКВИЗИТЫ СТОРОН", True),
        ("ПРИЛОЖЕНИЕ № 1 К ДОГОВОРУ № 12/345-67 ОТ 01.01.2026", True),
        # Номер госконтракта из 19 цифр и ИКЗ из 36: цифр больше, чем букв, но это заголовок.
        (f"ПРИЛОЖЕНИЕ № 1 К КОНТРАКТУ № {CONTRACT_19} ОТ «__» ______ 2026 Г.", True),
        (f"ПРИЛОЖЕНИЕ № 1 К КОНТРАКТУ № {IKZ_36}", True),
        ("Предмет договора", False),
        # Строки реквизитов заглавными, как бывает в госконтрактах.
        ("ИНН 7700000001, КПП 770001001, ОГРН 1027700000001.", False),
        ("ОКПО 12345678, ОКТМО 45000000000, ОКОГУ 1300000, ОКФС 12, ОКОПФ 75104,", False),
        ("ОГРНИП 323770000000017", False),
        ("ИНН/КПП 7700000001/770001001", False),
        ("Р/СЧ 40802810500000000001 В АО «ТЕСТБАНК»", False),
        # Метка реквизита — только целым словом в начале строки.
        ("ИННОВАЦИИ И РАЗВИТИЕ", True),
        ("БИКФОРДОВ ШНУР", True),
    ],
)
def test_is_heading(text: str, expected: bool) -> None:
    assert is_heading(raw(text)) is expected


def test_heading_style_wins_over_requisites_label() -> None:
    assert is_heading(raw("ИНН 7700000001, КПП 770001001", heading_level=0))


@pytest.mark.parametrize("number", [CONTRACT_19, IKZ_36])
def test_appendix_with_long_contract_number_starts_new_numbering(number: str) -> None:
    heading = f"ПРИЛОЖЕНИЕ № 1 К КОНТРАКТУ № {number} ОТ «__» ______ 2026 Г."
    blocks, warnings = segment(
        [
            raw("1. ПРЕДМЕТ"),
            raw("1.1. Поставщик обязуется поставить товар в срок, указанный в спецификации."),
            raw("1.2. Заказчик обязуется оплатить товар в течение десяти рабочих дней."),
            raw(heading),
            raw("1.1. Наименование товара указано в таблице ниже, количество — в штуках."),
            raw("1.2. Срок поставки по спецификации — тридцать календарных дней."),
        ]
    )
    assert [b.clause for b in blocks] == ["1", "1.1", "1.2", None, "1.1", "1.2"]
    assert blocks[3].kind == "heading" and blocks[4].section == heading
    assert warnings == []


def test_preamble_continuations_and_sections() -> None:
    blocks, warnings = segment(
        [
            raw("ДОГОВОР ПОСТАВКИ № 1"),
            raw("ООО «Покупатель», именуемое в дальнейшем «Покупатель»…"),
            raw("1. ПРЕДМЕТ ДОГОВОРА"),
            raw("1.1. Поставщик обязуется передать товар."),
            raw("Товар передаётся партиями."),
            raw("1) самовывозом;"),
            raw("1.2. Покупатель обязуется принять товар."),
        ]
    )
    assert [(b.clause, b.kind) for b in blocks] == [
        (None, "heading"),
        (None, "paragraph"),
        ("1", "heading"),
        ("1.1", "paragraph"),
        ("1.1", "paragraph"),
        ("1.1", "paragraph"),
        ("1.2", "paragraph"),
    ]
    assert blocks[3].section == "1. ПРЕДМЕТ ДОГОВОРА"
    assert blocks[1].section == "ДОГОВОР ПОСТАВКИ № 1"
    assert [b.id for b in blocks][:2] == ["B0001", "B0002"]
    assert warnings == []


def test_short_number_in_table_cell_is_row_number() -> None:
    blocks, _ = segment(
        [
            raw("1.1. Поставщик передаёт товар:"),
            raw("1.", table=(0, 1, 0)),
            raw("Изделие", table=(0, 1, 1)),
            raw("1.2. Поставщик гарантирует качество."),
        ]
    )
    assert [b.clause for b in blocks] == ["1.1", "1.1", "1.1", "1.2"]
    assert blocks[1].kind == "cell"


def test_long_numbered_text_in_table_is_clause() -> None:
    blocks, _ = segment([raw("3.1. Поставка осуществляется силами и за счёт Поставщика", table=(0, 0, 0))])
    assert blocks[0].clause == "3.1"


def test_appendix_starts_new_numbering_without_duplicate_warning() -> None:
    blocks, warnings = segment(
        [
            raw("1. ПРЕДМЕТ"),
            raw("1.1. Текст договора."),
            raw("ПРИЛОЖЕНИЕ № 1 К ДОГОВОРУ"),
            raw("Спецификация товара."),
            raw("1. Наименование товара."),
        ]
    )
    assert [b.clause for b in blocks] == ["1", "1.1", None, None, "1"]
    assert warnings == []


def test_duplicate_numbers_are_reported() -> None:
    _, warnings = segment([raw("5.1. Первый"), raw("5.2. Второй"), raw("5.2. Снова второй")])
    assert warnings == [DUPLICATE_WARNING.format(numbers="5.2")]


def test_empty_paragraphs_are_skipped() -> None:
    blocks, _ = segment([raw(""), raw("1.1. Текст"), raw("", label="2.")])
    assert [b.text for b in blocks] == ["1.1. Текст"]


def test_block_serialization() -> None:
    blocks, _ = segment([raw("1.1. Текст пункта о поставке товара", table=(0, 1, 2), page=3)])
    assert blocks[0].to_dict() == {
        "id": "B0001",
        "kind": "cell",
        "text": "1.1. Текст пункта о поставке товара",
        "clause": "1.1",
        "section": None,
        "label": None,
        "table": [0, 1, 2],
        "page": 3,
    }
