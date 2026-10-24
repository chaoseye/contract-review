"""Восстановление автонумерации Word: правила, которые используют реальные договоры."""

import time
from pathlib import Path

from app.documents.docx_reader import read_docx
from app.documents.numbering import format_number
from app.documents.segment import segment
from tests.docx_builder import abstract, level, make_docx, num, numbering, p, paragraph_style, styles


def labels(path: Path) -> list[str | None]:
    return [paragraph.label for paragraph in read_docx(path).paragraphs]


def multilevel(abstract_id: str = "1") -> str:
    return abstract(abstract_id, level(0) + level(1) + level(2))


def test_multilevel_decimal_and_restart_of_deeper_levels(tmp_path: Path) -> None:
    body = "".join(
        p(text, num="1", ilvl=ilvl)
        for text, ilvl in [("Предмет", 0), ("а", 1), ("б", 1), ("в", 2), ("Цена", 0), ("г", 1)]
    )
    path = make_docx(tmp_path / "a.docx", body, numbering_xml=numbering(multilevel(), num("1", "1")))
    assert labels(path) == ["1.", "1.1.", "1.2.", "1.2.1.", "2.", "2.1."]


def test_clause_numbers_come_from_labels(tmp_path: Path) -> None:
    body = (
        p("ПРЕДМЕТ ДОГОВОРА", num="1", ilvl=0) + p("Поставщик обязуется", num="1", ilvl=1) + p("Продолжение")
    )
    path = make_docx(tmp_path / "a.docx", body, numbering_xml=numbering(multilevel(), num("1", "1")))
    blocks, _ = segment(read_docx(path).paragraphs)
    assert [(b.text, b.clause) for b in blocks] == [
        ("1. ПРЕДМЕТ ДОГОВОРА", "1"),
        ("1.1. Поставщик обязуется", "1.1"),
        ("Продолжение", "1.1"),
    ]
    assert blocks[0].kind == "heading"


def test_lists_sharing_definition_continue_numbering(tmp_path: Path) -> None:
    body = p("а", num="1") + p("б", num="1") + p("в", num="2")
    xml = numbering(abstract("1", level(0)), num("1", "1"), num("2", "1"))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == ["1.", "2.", "3."]


def test_start_override_restarts_numbering(tmp_path: Path) -> None:
    body = p("а", num="1") + p("б", num="1") + p("в", num="2") + p("г", num="2")
    restart = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>'
    xml = numbering(abstract("1", level(0)), num("1", "1"), num("2", "1", restart))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == ["1.", "2.", "1.", "2."]


def test_start_value(tmp_path: Path) -> None:
    body = p("а", num="1") + p("б", num="1")
    xml = numbering(abstract("1", level(0, start=5)), num("1", "1"))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == ["5.", "6."]


def test_lvl_restart_zero_keeps_counting(tmp_path: Path) -> None:
    body = (
        p("а", num="1", ilvl=0) + p("б", num="1", ilvl=1) + p("в", num="1", ilvl=0) + p("г", num="1", ilvl=1)
    )
    levels = level(0) + level(1, text="(%2)", extra='<w:lvlRestart w:val="0"/>')
    xml = numbering(abstract("1", levels), num("1", "1"))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == ["1.", "(1)", "2.", "(2)"]


def test_is_legal_numbering_and_roman(tmp_path: Path) -> None:
    body = p("а", num="1", ilvl=0) + p("б", num="1", ilvl=1)
    levels = level(0, fmt="upperRoman", text="%1.") + level(1, text="%1.%2.", extra="<w:isLgl/>")
    xml = numbering(abstract("1", levels), num("1", "1"))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == ["I.", "1.1."]


def test_russian_letters_are_sub_items_not_clauses(tmp_path: Path) -> None:
    body = (
        p("Покупатель обязан:", num="1", ilvl=0)
        + p("принять товар;", num="1", ilvl=1)
        + p("оплатить.", num="1", ilvl=1)
    )
    levels = level(0, text="%1.") + level(1, fmt="russianLower", text="%2)")
    path = make_docx(tmp_path / "a.docx", body, numbering_xml=numbering(abstract("1", levels), num("1", "1")))
    blocks, _ = segment(read_docx(path).paragraphs)
    assert [(b.text, b.clause) for b in blocks] == [
        ("1. Покупатель обязан:", "1"),
        ("а) принять товар;", "1"),
        ("б) оплатить.", "1"),
    ]


def test_numbering_from_paragraph_style(tmp_path: Path) -> None:
    style_xml = styles(
        paragraph_style("Clause1", "Clause 1", num_id="1", ilvl=0),
        paragraph_style("Clause2", "Clause 2", num_id="1", ilvl=1),
    )
    body = p("Раздел", style="Clause1") + p("пункт", style="Clause2") + p("пункт", style="Clause2")
    path = make_docx(
        tmp_path / "a.docx", body, numbering_xml=numbering(multilevel(), num("1", "1")), styles_xml=style_xml
    )
    assert labels(path) == ["1.", "1.1.", "1.2."]


def test_level_linked_to_style_through_pstyle(tmp_path: Path) -> None:
    style_xml = styles(paragraph_style("Sub", "Sub clause", num_id="1"))
    levels = level(0) + level(1, extra='<w:pStyle w:val="Sub"/>')
    body = p("Раздел", num="1", ilvl=0) + p("пункт", style="Sub")
    path = make_docx(
        tmp_path / "a.docx",
        body,
        numbering_xml=numbering(abstract("1", levels), num("1", "1")),
        styles_xml=style_xml,
    )
    assert labels(path) == ["1.", "1.1."]


def test_num_id_zero_removes_style_numbering(tmp_path: Path) -> None:
    style_xml = styles(paragraph_style("Clause1", "Clause 1", num_id="1", ilvl=0))
    body = (
        p("с номером", style="Clause1")
        + p("без номера", style="Clause1", num="0")
        + p("снова", style="Clause1")
    )
    path = make_docx(
        tmp_path / "a.docx", body, numbering_xml=numbering(multilevel(), num("1", "1")), styles_xml=style_xml
    )
    assert labels(path) == ["1.", None, "2."]


def test_bullets_are_marked(tmp_path: Path) -> None:
    body = p("пункт", num="1", ilvl=0) + p("вариант", num="2")
    xml = numbering(
        multilevel(), abstract("2", level(0, fmt="bullet", text="●")), num("1", "1"), num("2", "2")
    )
    content = read_docx(make_docx(tmp_path / "a.docx", body, numbering_xml=xml))
    assert [(x.label, x.is_bullet) for x in content.paragraphs] == [("1.", False), ("●", True)]
    blocks, _ = segment(content.paragraphs)
    assert blocks[1].text == "— вариант"
    assert blocks[1].clause == "1"


def test_empty_numbered_paragraph_still_counts(tmp_path: Path) -> None:
    body = p("первый", num="1") + p("", num="1") + p("третий", num="1")
    xml = numbering(abstract("1", level(0)), num("1", "1"))
    content = read_docx(make_docx(tmp_path / "a.docx", body, numbering_xml=xml))
    blocks, _ = segment(content.paragraphs)
    assert [b.text for b in blocks] == ["1. первый", "3. третий"]


def test_unknown_num_id_is_ignored(tmp_path: Path) -> None:
    body = p("текст", num="99")
    xml = numbering(abstract("1", level(0)), num("1", "1"))
    assert labels(make_docx(tmp_path / "a.docx", body, numbering_xml=xml)) == [None]


def test_format_number() -> None:
    assert [format_number(n, "lowerLetter") for n in (1, 26, 27)] == ["a", "z", "aa"]
    assert [format_number(n, "russianLower") for n in (1, 2, 9, 10)] == ["а", "б", "и", "к"]
    assert format_number(14, "upperRoman") == "XIV"
    assert format_number(3, "decimalZero") == "03"
    assert format_number(7, "ordinalText") == "7"
    # Больше предела Word — цифрами: буквы и римские цифры растут вместе с номером.
    assert format_number(40000, "lowerRoman") == "40000"
    assert format_number(40000, "lowerLetter") == "40000"


def raw_level(start: str, fmt: str, ilvl: int = 0) -> str:
    return (
        f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
        '<w:lvlText w:val="%1."/></w:lvl>'
    )


def two_items(path: Path, xml: str) -> list[str | None]:
    return labels(make_docx(path, p("а", num="1") + p("б", num="1"), numbering_xml=xml))


def test_huge_start_values_are_clamped(tmp_path: Path) -> None:
    # Документ в несколько КБ с огромным w:start раньше читался бесконечно.
    cases = [("1e12", "lowerRoman"), ("2000000000", "lowerLetter"), ("2000000000", "russianLower")]
    for index, (start, fmt) in enumerate(cases):
        xml = numbering(abstract("1", raw_level(start, fmt)), num("1", "1"))
        started = time.monotonic()
        result = two_items(tmp_path / f"{index}.docx", xml)
        assert time.monotonic() - started < 5
        assert all(label is not None and len(label) <= 50 for label in result), (start, fmt)
    xml = numbering(abstract("1", raw_level("-5", "decimal")), num("1", "1"))
    assert two_items(tmp_path / "neg.docx", xml) == ["0.", "1."]


def test_unreadable_numbers_fall_back_to_defaults(tmp_path: Path) -> None:
    # «1e999» — бесконечность: раньше OverflowError обрывал чтение документа.
    override = '<w:lvlOverride w:ilvl="1e999"><w:startOverride w:val="nan"/></w:lvlOverride>'
    xml = numbering(abstract("1", raw_level("1e999", "decimal")), num("1", "1", override))
    assert two_items(tmp_path / "a.docx", xml) == ["1.", "2."]


def test_levels_outside_word_range_are_ignored(tmp_path: Path) -> None:
    # Уровней в Word девять: w:ilvl="12" в lvlOverride раньше давал IndexError.
    override = '<w:lvlOverride w:ilvl="12"><w:startOverride w:val="7"/></w:lvlOverride>'
    xml = numbering(abstract("1", level(0) + raw_level("3", "decimal", ilvl=12)), num("1", "1", override))
    assert two_items(tmp_path / "a.docx", xml) == ["1.", "2."]


def test_long_level_text_is_cut(tmp_path: Path) -> None:
    xml = numbering(abstract("1", level(0, text="%1." + "x" * 10_000)), num("1", "1"))
    result = labels(make_docx(tmp_path / "a.docx", p("а", num="1"), numbering_xml=xml))
    assert result[0] is not None and result[0].startswith("1.") and len(result[0]) == 50
