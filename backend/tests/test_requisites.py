"""Наша сторона и сверка реквизитов без ИИ на синтетических договорах.

Реквизиты вымышленные. Основной договор (`typical`) собирается в тесте: мы — поставщик, реквизиты как в CARD.
"""

from pathlib import Path
from typing import Any

import pytest

from app.analysis.prompt import CheckInfo, build_request
from app.analysis.requisites import check_side
from app.documents.extract import extract_document
from app.models import Organization
from tests.docx_builder import make_docx, p, table

CARD = {
    "inn": "770000000156",
    "ogrnip": "323770000000017",
    "legal_address": "Московская обл., г. Пример, ул. Тестовая, д. 1",
    "postal_address": "100000, г. Пример, ул. Тестовая, д. 2, а/я 1",
    "bik": "044525999",
    "account": "40802810500000000001",
    "corr_account": "30101810600000000999",
    "email": "Orders@Example.ru",
    "phone": "+7 900 000-00-00; 8 (900) 000-00-01",
}


def organization(**requisites: str) -> Organization:
    return Organization(
        full_name="Индивидуальный предприниматель Петров Пётр Петрович",
        short_name="ИП Петров Пётр Петрович",
        requisites={**CARD, **requisites},
    )


def typical_contract() -> str:
    """Договор поставки: заголовок, город, преамбула с нами, пункты и таблица реквизитов —
    поставщик слева, покупатель справа, подписи строкой ниже."""
    supplier = cells(
        "ПОСТАВЩИК:",
        "Индивидуальный предприниматель Петров Пётр Петрович",
        "Юр. адрес: Московская обл., г. Пример, ул. Тестовая, д. 1",
        "Почт. адрес: 100000, г. Пример, ул. Тестовая, д. 2, а/я 1",
        "ИНН 770000000156",
        "ОГРНИП 323770000000017",
        "Реквизиты банка: АО «Тестбанк»",
        "БИК 044525999",
        "к/с 30101810600000000999",
        "р/с 40802810500000000001",
        "Тел.: +7 (900) 000-00-00",
        "Email: orders@example.ru",
    )
    buyer = cells(
        "ПОКУПАТЕЛЬ:",
        "ООО «Ромашка»",
        "Юр. адрес: 101000, г. Пример, ул. Садовая, д. 3",
        "ИНН 7700000003",
        "Тел.: +7 (495) 111-11-11",
        "Email: zakaz@romashka.example",
    )
    return (
        p("Договор поставки № 1 от 01.02.2026")
        + p("г. Пример")
        + p(
            "Индивидуальный предприниматель Петров Пётр Петрович, ИНН 770000000156, ОГРНИП 323770000000017, "
            "именуемый в дальнейшем «Поставщик», с одной стороны, и ООО «Ромашка», именуемое в дальнейшем "
            "«Покупатель», с другой стороны, заключили настоящий договор о нижеследующем."
        )
        + clauses(11, caps=True)
        + p("12. АДРЕСА, БАНКОВСКИЕ РЕКВИЗИТЫ И ПОДПИСИ СТОРОН")
        + table(
            [
                [supplier, buyer],
                [cells("_______________ / Петров П.П./", "М.П."), cells("___________ /Иванов И.И./", "М.П.")],
            ]
        )
    )


@pytest.fixture(scope="module")
def typical(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    work = tmp_path_factory.mktemp("typical")
    return extracted(make_docx(work / "contract.docx", typical_contract()), work)


def block(block_id: str, text: str, kind: str = "paragraph", clause: str | None = None) -> dict[str, Any]:
    return {"id": block_id, "kind": kind, "text": text, "clause": clause, "page": 1}


def test_typical_contract_matches_card(typical) -> None:
    report = check_side(typical, organization())
    assert report.status == "found"
    assert (report.role, report.role_word) == ("supplier", "Поставщик")
    assert set(report.found_by) == {"inn", "name"}
    assert report.preamble_block == "B0003"
    statuses = {check.key: check.status for check in report.checks}
    assert statuses == dict.fromkeys(CARD, "match")
    # Найденное значение показывается как в договоре, а не в нижнем регистре.
    postal = next(check for check in report.checks if check.key == "postal_address")
    assert postal.found == ["100000, г. Пример, ул. Тестовая, д. 2, а/я 1"]
    assert report.warnings == []
    assert report.prompt_note() == (
        "По ИНН и наименованию наша организация найдена в договоре, её роль по тексту — «Поставщик»."
    )


def test_postal_address_without_po_box_is_a_mismatch(typical) -> None:
    # В карточке адрес без абонентского ящика, в договоре — с ним (или наоборот).
    report = check_side(typical, organization(postal_address="100000, г. Пример, ул. Тестовая, д. 2"))
    postal = next(check for check in report.checks if check.key == "postal_address")
    assert postal.status == "mismatch"
    assert report.warnings == [
        "Реквизиты поставщика в договоре расходятся с карточкой организации: почтовый адрес."
    ]


def test_wrong_account_and_email(typical) -> None:
    report = check_side(typical, organization(account="40802810500000000002", email="sales@example.ru"))
    statuses = {check.key: check.status for check in report.checks}
    assert statuses["account"] == statuses["email"] == "mismatch"
    assert "расчётный счёт, e-mail" in report.warnings[-1]


def test_address_spelling_differences_still_match(typical) -> None:
    report = check_side(
        typical, organization(legal_address="Московская область, г Пример, улица Тестовая, дом 1")
    )
    legal = next(check for check in report.checks if check.key == "legal_address")
    assert legal.status == "match"


def test_we_are_named_buyer() -> None:
    blocks = [
        block(
            "B0001",
            "Индивидуальный предприниматель Петров Пётр Петрович, именуемый в дальнейшем «Покупатель», "
            "с одной стороны, и ООО «Ромашка», ИНН 7700000001, именуемое в дальнейшем «Поставщик», "
            "с другой стороны, заключили договор.",
        ),
        block("B0002", "1. ПРЕДМЕТ", kind="heading", clause="1"),
    ]
    report = check_side(blocks, organization())
    assert (report.status, report.role, report.role_word) == ("found", "buyer", "Покупатель")
    assert "названа «Покупатель», а не «Поставщик»" in report.warnings[0]
    assert "Блок реквизитов поставщика в договоре не найден" in report.warnings[1]
    assert "«Покупатель»" in (report.prompt_note() or "")


def test_role_from_requisites_header_and_inn_mismatch() -> None:
    blocks = [
        block("B0001", "Договор поставки № 5"),
        block("B0002", "Стороны договорились о нижеследующем."),
        block("B0003", "1.1. Поставщик передаёт товар.", clause="1.1"),
        block("B0004", "12. РЕКВИЗИТЫ СТОРОН", kind="heading", clause="12"),
        block("B0005", "ПОСТАВЩИК:", kind="cell", clause="12"),
        block("B0006", "ИП Петров П.П.", kind="cell", clause="12"),
        block("B0007", "ИНН 770000000157", kind="cell", clause="12"),
        block("B0008", "ПОКУПАТЕЛЬ:", kind="cell", clause="12"),
        block("B0009", "ООО «Ромашка», ИНН 7700000001, тел. 8 900 111 22 33", kind="cell", clause="12"),
    ]
    report = check_side(blocks, organization())
    assert report.status == "found" and report.found_by == ["name"]
    assert (report.role, report.requisites_block) == ("supplier", "B0005")
    statuses = {check.key: check.status for check in report.checks}
    # Чужой телефон и ИНН покупателя в сверку не попадают: блок реквизитов кончается на «ПОКУПАТЕЛЬ:».
    assert statuses["inn"] == "mismatch" and statuses["phone"] == "not_found"
    assert "ИНН".lower() in report.warnings[-1]


def test_not_found_and_skipped(typical) -> None:
    stranger = Organization(full_name="ООО «Василёк»", short_name="", requisites={"inn": "7700000001"})
    report = check_side(typical, stranger)
    assert report.status == "not_found" and report.checks == []
    assert "не нашли в договоре" in report.warnings[0]
    assert "найти не удалось" in (report.prompt_note() or "")

    assert check_side(typical, None).status == "skipped"
    empty = Organization(full_name="", short_name="", requisites={})
    skipped = check_side(typical, empty)
    assert skipped.status == "skipped" and skipped.prompt_note() is None


def test_legal_entity_inn_is_not_a_phone() -> None:
    blocks = [
        block("B0001", "ООО «Василёк», именуемое в дальнейшем «Поставщик», с одной стороны"),
        *[block(f"B00{index:02d}", f"{index}. Текст пункта", clause=str(index)) for index in range(2, 10)],
        block("B0010", "Поставщик: ООО «Василёк»\nИНН 7700000001\nТел.: +7 (495) 000-00-00", kind="cell"),
    ]
    company = Organization(
        full_name="ООО «Василёк»",
        short_name="",
        requisites={"inn": "7700000001", "phone": "+7 495 000 00 00"},
    )
    report = check_side(blocks, company)
    statuses = {check.key: (check.status, check.found) for check in report.checks}
    assert statuses["inn"] == ("match", ["7700000001"])
    assert statuses["phone"] == ("match", ["+7 (495) 000-00-00"])


def test_role_goes_to_prompt_without_requisites(typical) -> None:
    report = check_side(typical, organization())
    info = CheckInfo(side_note=report.prompt_note())
    request = build_request(typical[:3], info)
    assert "её роль по тексту — «Поставщик»" in request.user
    # Сами реквизиты из карточки в сведения о проверке не попадают.
    info_part = request.user.split("ДОГОВОР", 1)[0]
    assert CARD["account"] not in info_part and CARD["inn"] not in info_part


def test_email_on_organization_domain_is_ours(typical) -> None:
    # Правильные — и адрес из карточки, и адреса на домене организации.
    card_only_domain = organization(email="", email_domains="example.ru")
    check = next(item for item in check_side(typical, card_only_domain).checks if item.key == "email")
    assert check.status == "match" and check.expected == "любой адрес на example.ru"

    other_card_email = organization(email="sales@example.ru", email_domains="@Example.RU, example.com")
    check = next(item for item in check_side(typical, other_card_email).checks if item.key == "email")
    assert check.status == "match"
    assert check.expected == "sales@example.ru; любой адрес на example.ru; любой адрес на example.com"

    wrong_domain = organization(email="sales@example.com", email_domains="example.com")
    report = check_side(typical, wrong_domain)
    check = next(item for item in report.checks if item.key == "email")
    assert check.status == "mismatch" and "e-mail" in report.warnings[-1]


def test_foreign_email_next_to_ours_is_a_mismatch() -> None:
    blocks = [
        block("B0001", "ООО «Василёк», именуемое в дальнейшем «Поставщик», с одной стороны"),
        *[block(f"B00{index:02d}", f"{index}. Текст пункта", clause=str(index)) for index in range(2, 10)],
        block(
            "B0010",
            "Поставщик: ООО «Василёк»\nИНН 7700000001\nE-mail: info@vasilek.ru, info@vasi1ek.ru",
            kind="cell",
        ),
    ]
    company = Organization(
        full_name="ООО «Василёк»",
        short_name="",
        requisites={"inn": "7700000001", "email_domains": "vasilek.ru"},
    )
    check = next(item for item in check_side(blocks, company).checks if item.key == "email")
    assert check.status == "mismatch" and check.found == ["info@vasilek.ru", "info@vasi1ek.ru"]


# Госконтракты: реквизиты таблицей в две колонки и абзацами. Реквизиты вымышленные.
GOV_CARD = {
    "legal_address": "140000, Московская обл., м. р-н Примерный, с.п. Тестовое, ул. Тестовая, д. 1",
    "actual_address": "100000, г. Пример, ул. Тестовая, д. 2",
}


def clauses(count: int, *, caps: bool) -> str:
    title = "УСЛОВИЯ КОНТРАКТА" if caps else "Условия контракта"
    return "".join(
        p(f"{n}. {title}") + p(f"{n}.1. Текст пункта {n}.1 контракта.") for n in range(1, count + 1)
    )


def cells(*lines: str) -> str:
    return "".join(p(line) for line in lines)


def extracted(path: Path, work: Path) -> list[dict[str, Any]]:
    return [item.to_dict() for item in extract_document(path, work).blocks]


def test_requisites_table_in_two_columns(tmp_path: Path) -> None:
    # Государственный заказчик слева, поставщик справа, ячейки читаются по строкам вперемешку.
    requisites = table(
        [
            [p("Государственный заказчик:"), p("Поставщик:")],
            [
                cells("ФКУ «Склад № 1»", "(ФКУ «Склад»)"),
                p("Индивидуальный предприниматель Петров Пётр Петрович"),
            ],
            [p("ИНН 7700000001"), cells("ИНН 770000000156", "Дата постановки на учёт 01.02.2023")],
            [p("КПП 770001001"), p("")],
            [p("ОГРН 1027700000001"), p("ОГРНИП 323770000000017")],
            [p("ОКПО 12345678"), p("ОКПО 1234567890")],
            [
                cells("БАНКОВСКИЕ РЕКВИЗИТЫ:", "Казначейский счёт", "03221643000000000001", "БИК 024501901"),
                cells(
                    "БАНКОВСКИЕ РЕКВИЗИТЫ:",
                    "АО «Тестбанк»",
                    "р/с 40802810500000000001",
                    "к/с 30101810600000000999",
                    "БИК 044525999",
                ),
            ],
            [
                p("Адрес: 101000, г. Пример, ул. Казённая, д. 5"),
                # Юридический адрес разбит на абзацы одной ячейки.
                cells(
                    "Юридический адрес: 140000,",
                    "Московская обл,",
                    "м. р-н Примерный,",
                    "с.п. Тестовое,",
                    "ул Тестовая, д. 1",
                ),
            ],
            # Лишняя цифра в индексе, остальное как в карточке.
            [
                p("Почтовый адрес:101000, г. Пример, а/я 7"),
                p("Почтовый адрес: 1000001, г. Пример, ул. Тестовая, д. 2, а/я 1"),
            ],
            [
                cells(
                    "тел. (495) 111-11-11",
                    "электронная почта:",
                    "sklad@example.gov",
                    "____ И.И. Иванов",
                    "МП",
                ),
                cells(
                    "тел.+7-900-000-00-00",
                    "Электронная почта:",
                    "orders@example.ru",
                    "____ П.П. Петров",
                    "МП",
                ),
            ],
        ]
    )
    body = (
        p("Государственный контракт № 1/234-56")
        + p(
            "Федеральное казённое учреждение «Склад № 1» (ФКУ «Склад»), именуемое в дальнейшем "
            "«Государственный заказчик», в лице начальника Иванова И.И., с одной стороны, и Индивидуальный "
            "предприниматель Петров Пётр Петрович, именуемый в дальнейшем «Поставщик», с другой стороны, "
            "заключили настоящий контракт."
        )
        + clauses(10, caps=True)
        + p("11. АДРЕСА И БАНКОВСКИЕ РЕКВИЗИТЫ СТОРОН")
        + requisites
        + p("СПЕЦИФИКАЦИЯ")
        + table([[p("№"), p("Наименование")], [p("1"), p("Изделие")]])
        + p("ПОДПИСИ СТОРОН:")
        + table(
            [
                [
                    p("____ И.И. Иванов"),
                    cells("Поставщик", "Индивидуальный предприниматель", "____ П.П. Петров"),
                ]
            ]
        )
    )
    blocks = extracted(make_docx(tmp_path / "gov.docx", body), tmp_path)
    # Ячейки таблиц — отдельные блоки, поэтому наш ИНН оказался в первой половине договора.
    our_inn = next(index for index, block in enumerate(blocks) if block["text"] == "ИНН 770000000156")
    assert our_inn < len(blocks) // 2
    report = check_side(blocks, organization(legal_address=GOV_CARD["legal_address"]))

    assert (report.status, report.role) == ("found", "supplier")
    assert set(report.found_by) == {"inn", "name"}
    assert report.requisites_block == next(block["id"] for block in blocks if block["text"] == "Поставщик:")
    checks = {check.key: (check.status, check.found) for check in report.checks}
    # Сверены все реквизиты колонки поставщика; ИНН, ОГРН и e-mail заказчика в сверку не попали.
    assert checks == {
        "inn": ("match", ["770000000156"]),
        "ogrnip": ("match", ["323770000000017"]),
        "legal_address": (
            "match",
            ["140000, Московская обл, м. р-н Примерный, с.п. Тестовое, ул Тестовая, д. 1"],
        ),
        "postal_address": ("mismatch", ["1000001, г. Пример, ул. Тестовая, д. 2, а/я 1"]),
        "bik": ("match", ["044525999"]),
        "account": ("match", ["40802810500000000001"]),
        "corr_account": ("match", ["30101810600000000999"]),
        "email": ("match", ["orders@example.ru"]),
        "phone": ("match", ["+7-900-000-00-00"]),
    }
    assert report.warnings == [
        "Реквизиты поставщика в договоре расходятся с карточкой организации: почтовый адрес."
    ]


def test_requisites_in_paragraphs_with_caps_lines(tmp_path: Path) -> None:
    # Как во втором госконтракте: реквизиты абзацами, в строках «ИНН …, КПП …» и «ОГРНИП …» нет строчных.
    lines = [
        "Заказчик:",
        "ФГКВОУ ВО «Тестовая академия»",
        "Юридический адрес: 101000, г. Пример, ул. Учебная, д. 10,",
        "Фактический адрес: 101000, г. Пример, ул. Учебная, д. 10.",
        "Тел.: (4950) 11-11-11, E-mail: academy@example.gov,",
        "ИНН 7700000001, КПП 770001001, ОГРН 1027700000001.",
        "Номер казначейского счёта 03221643000000000001,",
        "ОКПО 12345678, ОКТМО 45000000000, ОКОГУ 1300000, ОКФС 12, ОКОПФ 75104,",
        "Заказчик ____________ И.И. Иванов",
        "Поставщик:",
        "ИП Петров П.П.",
        "ИНН 770000000156",
        "ОГРНИП 323770000000017",
        "Юридический адрес: 140000, Московская обл, м.р-н Примерный, с.п. Тестовое, ул Тестовая, д. 1",
        "Почтовый адрес: 100500, г. Пример, ул. Другая 2/15, кв.101",
        "Фактический адрес: 100000, г. Пример, ул. Тестовая, 2",
        "E-mail: orders@example.ru",
        "Телефон +79000000000",
        "Наименование банка: АО «Тестбанк» г. Пример",
        "Номер расчётного счёта 40802810500000000001",
        "БИК 044525999",
        "Корреспондентский счёт 30101810600000000999",
        "Поставщик ____________ П.П. Петров",
    ]
    body = (
        p("ГОСУДАРСТВЕННЫЙ КОНТРАКТ № 1/234-56")
        + p(
            "Индивидуальный предприниматель Петров Пётр Петрович (ИП Петров П.П.), именуемый в дальнейшем "
            "«Поставщик», с одной стороны, и ФГКВОУ ВО «Тестовая академия», именуемое в дальнейшем "
            "«Заказчик», с другой стороны, заключили настоящий контракт."
        )
        + clauses(14, caps=False)
        + p("15. Юридические адреса, банковские реквизиты и подписи Сторон")
        + cells(*lines)
    )
    blocks = extracted(make_docx(tmp_path / "gov.docx", body), tmp_path)
    by_text = {block["text"]: block for block in blocks}
    for line in (lines[5], lines[7], lines[12]):
        assert by_text[line]["kind"] == "paragraph"
    # Раздел у всех блоков — заголовок контракта, а не строка реквизитов.
    assert {block["section"] for block in blocks} == {"ГОСУДАРСТВЕННЫЙ КОНТРАКТ № 1/234-56"}

    report = check_side(blocks, organization(**GOV_CARD))
    assert (report.status, report.role, report.role_word) == ("found", "supplier", "Поставщик")
    assert report.requisites_block == by_text["Поставщик:"]["id"]
    statuses = {check.key: check.status for check in report.checks}
    assert statuses == {**dict.fromkeys([*CARD, "actual_address"], "match"), "postal_address": "mismatch"}
    postal = next(check for check in report.checks if check.key == "postal_address")
    assert postal.found == ["100500, г. Пример, ул. Другая 2/15, кв.101"]


def test_address_lines_of_one_cell_end_at_next_label() -> None:
    # Ячейка PDF — один блок со строками: адрес собирается до названия банка или строки с датой,
    # счета читаются отдельно.
    cell = block(
        "B0010",
        "Поставщик: ИП Петров П.П.\nИНН 770000000156\nЮридический адрес: 140000,\nМосковская обл,\n"
        "ул Тестовая, д. 1\nАО «Тестбанк»\nР/счёт № 40802810500000000001\nКорр/сч 30101810600000000999\n"
        "Почтовый адрес: 100000, г. Пример,\nул. Тестовая, д. 2, а/я 1\nзарегистрирован 01.02.2023",
        kind="cell",
    )
    blocks = [
        block("B0001", "ИП Петров Пётр Петрович, именуемый в дальнейшем «Поставщик», с одной стороны"),
        *[block(f"B00{index:02d}", f"{index}. Текст пункта", clause=str(index)) for index in range(2, 10)],
        {**cell, "table": [0, 0, 0]},
    ]
    report = check_side(blocks, organization(legal_address="140000, Московская обл., ул. Тестовая, д. 1"))
    checks = {check.key: (check.status, check.found) for check in report.checks}
    assert checks["legal_address"] == ("match", ["140000, Московская обл, ул Тестовая, д. 1"])
    assert checks["postal_address"] == ("match", ["100000, г. Пример, ул. Тестовая, д. 2, а/я 1"])
    # «Корр/сч» — корреспондентский счёт, а не расчётный.
    assert checks["account"] == ("match", ["40802810500000000001"])
    assert checks["corr_account"] == ("match", ["30101810600000000999"])


def test_other_party_above_us_in_the_same_column() -> None:
    # Стороны одна под другой в одной колонке и без заголовков: ячейка покупателя в сверку не попадает.
    column = [
        (0, "ООО «Ромашка»"),
        (0, "ИНН 7700000001"),
        (0, "Тел.: 8 900 111 22 33"),
        (1, "ИП Петров П.П."),
        (1, "ИНН 770000000156"),
        (1, "Тел.: +7 900 000-00-00"),
    ]
    blocks = [
        block("B0001", "ИП Петров Пётр Петрович, именуемый в дальнейшем «Поставщик», с одной стороны"),
        *[block(f"B00{index:02d}", f"{index}. Текст пункта", clause=str(index)) for index in range(2, 10)],
        *[
            {**block(f"B00{10 + offset}", text, kind="cell"), "table": [0, row, 0]}
            for offset, (row, text) in enumerate(column)
        ],
    ]
    report = check_side(blocks, organization())
    assert report.requisites_block == "B0013"
    checks = {check.key: (check.status, check.found) for check in report.checks}
    assert checks["inn"] == ("match", ["770000000156"])
    assert checks["phone"] == ("match", ["+7 900 000-00-00"])


@pytest.mark.parametrize("ours_first", [True, False])
def test_party_per_row_table(tmp_path: Path, ours_first: bool) -> None:
    # Таблица «сторона в строке»: слева «Поставщик:» и «Покупатель:», справа реквизиты. У покупателя
    # ИНН последний или его нет вовсе: его адрес и e-mail не должны попасть в нашу сверку.
    ours = [
        p("Поставщик:"),
        cells(
            "Индивидуальный предприниматель Петров Пётр Петрович",
            "Почтовый адрес: 100000, г. Пример, ул. Тестовая, д. 2, а/я 1",
            "ИНН 770000000156",
            "E-mail: orders@example.ru",
        ),
    ]
    buyer = [
        p("Покупатель:"),
        cells(
            "ООО «Ромашка»",
            "Почтовый адрес: 200000, г. Другой, ул. Иная, д. 9",
            "E-mail: buyer@example.com",
            *(["ИНН 5000000000"] if ours_first else []),
        ),
    ]
    body = (
        p("ДОГОВОР ПОСТАВКИ № 5")
        # Роли в преамбуле нет: она берётся из заголовка в соседней ячейке нашей строки.
        + p("Индивидуальный предприниматель Петров Пётр Петрович и ООО «Ромашка» заключили договор.")
        + clauses(10, caps=True)
        + p("11. РЕКВИЗИТЫ СТОРОН")
        + table([ours, buyer] if ours_first else [buyer, ours])
    )
    blocks = extracted(make_docx(tmp_path / "rows.docx", body), tmp_path)
    report = check_side(blocks, organization())

    assert (report.role, report.role_word) == ("supplier", "Поставщик")
    our_name = next(
        block["id"]
        for block in blocks
        if block["kind"] == "cell" and block["text"].startswith("Индивидуальный")
    )
    assert report.requisites_block == our_name
    checks = {check.key: (check.status, check.found) for check in report.checks if check.found}
    assert checks == {
        "inn": ("match", ["770000000156"]),
        "postal_address": ("match", ["100000, г. Пример, ул. Тестовая, д. 2, а/я 1"]),
        "email": ("match", ["orders@example.ru"]),
    }
    assert report.warnings == []
