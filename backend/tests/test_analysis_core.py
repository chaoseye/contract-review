"""Анализ без провайдера: схема ответа, сборка промпта, проверка цитат и ссылок, переспрос, оценка."""

import pytest
from pydantic import ValidationError

from app.analysis.default_prompt import BASE_PROMPT, DEFAULT_ADMIN_PROMPT, POSITIONS_BLOCK
from app.analysis.evaluate import ExpectedProtocol, evaluate
from app.analysis.fixed_part import FIXED_PART
from app.analysis.prompt import CheckInfo, build_request, prompt_fingerprint, render_contract, with_feedback
from app.analysis.providers.base import ProviderResponseError
from app.analysis.run import run_analysis
from app.analysis.schema import AnalysisAnswer, response_schema
from app.analysis.validate import ContractIndex, check_answer, normalize, normalize_clause
from app.documents.model import RawParagraph
from app.documents.segment import segment
from tests.analysis_helpers import BLOCKS, FakeProvider, answer, risk

# --- схема ------------------------------------------------------------------------------------


def _objects(node, path="#"):
    if isinstance(node, dict):
        if "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _objects(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _objects(item, f"{path}/{index}")


def _all_dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _all_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _all_dicts(item)


def test_schema_is_strict() -> None:
    schema = response_schema()
    objects = list(_objects(schema))
    assert len(objects) >= 10
    for path, node in objects:
        assert node["additionalProperties"] is False, path
        assert sorted(node["required"]) == sorted(node["properties"]), path
        for name, field in node["properties"].items():
            # У ссылки на другую схему не должно быть соседних ключей: strict-режим их не принимает.
            if "$ref" in field:
                assert list(field) == ["$ref"], f"{path}/{name}"
    # Служебных title нет; title встречается только как имя поля риска.
    assert all("title" not in node or isinstance(node["title"], dict) for node in _all_dicts(schema))
    # Все девять разделов итогового формата есть в ответе.
    assert set(schema["properties"]) == {
        "contract",
        "summary",
        "approval",
        "confidence",
        "top_risks",
        "risks",
        "obligations",
        "contradictions",
        "missing",
        "favorable",
        "specialist_checks",
    }
    assert "title" in schema["$defs"]["Risk"]["properties"]
    assert response_schema() is not response_schema()


def test_answer_validation() -> None:
    parsed = AnalysisAnswer.model_validate(answer(contract={**answer()["contract"], "date": "31.07.2026"}))
    assert parsed.contract.date == "2026-07-31"
    assert (
        AnalysisAnswer.model_validate(answer(contract={**answer()["contract"], "date": "июль"})).contract.date
        is None
    )
    with pytest.raises(ValidationError, match="повторяются id"):
        AnalysisAnswer.model_validate(answer(risks=[risk("R1"), risk("R1")]))
    with pytest.raises(ValidationError):
        AnalysisAnswer.model_validate(answer(risks=[risk(severity="critical")]))
    with pytest.raises(ValidationError):
        AnalysisAnswer.model_validate({**answer(), "extra_field": 1})


# --- промпт -----------------------------------------------------------------------------------


def test_default_prompt_parts() -> None:
    assert f"{BASE_PROMPT}\n\n{POSITIONS_BLOCK}" == DEFAULT_ADMIN_PROMPT
    assert POSITIONS_BLOCK.startswith("ПОЗИЦИИ ПОСТАВЩИКА")


def test_request_assembly() -> None:
    info = CheckInfo(
        procurement="44fz",
        comment="Сумма 1,2 млн, предоплата 50%",
        ocr_used=True,
        page_count=13,
        warnings=["Повторяются номера пунктов: 9.2"],
    )
    request = build_request(BLOCKS, info)
    assert request.system.startswith(BASE_PROMPT[:200])
    assert request.system.endswith(FIXED_PART)
    assert POSITIONS_BLOCK in request.system
    assert request.schema == response_schema()
    assert request.schema_name == "contract_analysis"
    user = request.user
    assert "Режим закупки: госконтракт по 44-ФЗ." in user
    assert "Текст распознан со скана, страниц: 13" in user
    assert "При чтении документа: Повторяются номера пунктов: 9.2" in user
    assert "Параметры сделки и что учесть (от менеджера): Сумма 1,2 млн, предоплата 50%" in user
    assert "[B0001] (стр. 3, п. 9, заголовок) 9. ОТВЕТСТВЕННОСТЬ СТОРОН" in user
    assert "[B0002] (стр. 3, п. 9.2) 9.2. В случае просрочки" in user
    assert user.index("СВЕДЕНИЯ О ПРОВЕРКЕ") < user.index("ДОГОВОР\n[B0001]")
    plain = build_request(BLOCKS, CheckInfo())
    assert "Режим закупки: не указан менеджером." in plain.user
    assert "Комментария менеджера нет." in plain.user
    assert "распознан" not in plain.user
    assert len(prompt_fingerprint(DEFAULT_ADMIN_PROMPT)) == 12


def test_block_rendering_and_feedback() -> None:
    blocks = [
        {"id": "B0007", "kind": "cell", "text": "ПОСТАВЩИК:\nИП Пример\n", "clause": None, "page": None}
    ]
    assert render_contract(blocks) == "[B0007] (таблица) ПОСТАВЩИК: / ИП Пример"
    assert render_contract([{"id": "B0001", "kind": "paragraph", "text": "Преамбула"}]) == "[B0001] Преамбула"
    request = build_request(BLOCKS, CheckInfo())
    retry = with_feedback(request, "- risks.0.severity: Input should be 'high'")
    assert retry.system == request.system
    assert retry.user.startswith(request.user)
    assert "ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРИНЯТ" in retry.user


# --- проверка цитат и ссылок ---------------------------------------------------------------------


def test_normalization() -> None:
    assert normalize("«Неустойка» — 0,5 %  в\u00a0день…") == '"неустойка" - 0,5% в день'
    assert normalize("Ёмкость ( 5 шт )") == "емкость (5 шт)"
    assert normalize_clause("п. 9.2.") == "9.2"
    assert normalize_clause("пп.6.1") == "6.1"
    assert normalize_clause("пп. 6.1") == "6.1"
    assert normalize_clause("п. 2.3") == "2.3"
    assert normalize_clause("пункт 4") == "4"
    assert normalize_clause("9.2") == "9.2"


def test_clause_normalization_of_appendices() -> None:
    same = ["Приложение № 1", "Приложение 1", "прил. 1", "Приложение №1 «Спецификация»", "ПРИЛОЖЕНИЕ N 1"]
    assert {normalize_clause(clause) for clause in same} == {"приложение1"}
    assert normalize_clause("Приложение № 1 – Спецификация") == "приложение1"
    assert normalize_clause("Спецификация (Приложение № 1)") == "приложение1"
    assert normalize_clause("Приложение № 1 к контракту от 01.02.2026") == "приложение1"
    assert normalize_clause("Приложение № 2") == "приложение2"
    # Пункт внутри приложения — уровнем ниже приложения.
    assert normalize_clause("п. 3 Приложения № 1") == "приложение1.3"
    assert normalize_clause("Приложение № 1, пункт 2.1") == "приложение1.2.1"
    assert {normalize_clause("Спецификация"), normalize_clause("СПЕЦИФИКАЦИЯ")} == {"спецификация"}
    assert normalize_clause("Спецификация № 2") == "спецификация2"
    # «п» снимается только перед номером: раньше «Приложение» превращалось в «риложение».
    assert normalize_clause("подпункт 6.1.2") == "6.1.2"
    assert normalize_clause("п.п. 6.1") == "6.1"
    assert normalize_clause("преамбула") == "преамбула"
    assert normalize_clause("Приложение") == "приложение"


def test_appendix_is_not_a_clause_number() -> None:
    # Проверка номеров пунктов: «Приложение 9» — не пункт 9. Приложений в BLOCKS нет.
    index = ContractIndex(BLOCKS)
    assert index.known_clause("п. 9.2") and index.known_clause("подпункт 9.8") and index.known_clause("9")
    assert not index.known_clause("Приложение 9")
    assert not index.known_clause("п. 2 Приложения № 9")
    assert not index.known_clause("Спецификация")
    data = answer(risks=[risk("R1", clauses=["9.2", "Приложение № 1"])])
    checked = check_answer(AnalysisAnswer.model_validate(data), BLOCKS)
    assert checked.risk_checks["R1"]["unknown_clauses"] == ["Приложение № 1"]


def _marked_up(*paragraphs: RawParagraph) -> list[dict]:
    blocks, _ = segment(list(paragraphs))
    return [block.to_dict() for block in blocks]


# Госконтракт: перечень приложений в п. 8.3, реквизиты, затем приложение —
# строки «Приложение 1», «к контракту …» и заголовок «СПЕЦИФИКАЦИЯ» над таблицей.
GOVERNMENT_CONTRACT = (
    RawParagraph("8. ПРОЧИЕ УСЛОВИЯ"),
    RawParagraph("8.2. Характеристики товара указаны в Приложении № 2 к контракту."),
    RawParagraph("8.3. К контракту прилагаются и являются его неотъемлемой частью:"),
    RawParagraph("Приложение № 1 – Спецификация."),
    RawParagraph("9. АДРЕСА И РЕКВИЗИТЫ СТОРОН"),
    RawParagraph("Поставщик:", table=(0, 0, 1)),
    RawParagraph("Приложение 1"),
    RawParagraph("к контракту"),
    RawParagraph("от 01.02.2026 № 12/2026"),
    RawParagraph("СПЕЦИФИКАЦИЯ"),
    RawParagraph("Наименование", table=(1, 0, 1)),
    RawParagraph("1", table=(1, 1, 0)),
    RawParagraph("Изделие", table=(1, 1, 1)),
)


def test_attached_appendix_is_known() -> None:
    # Приложение есть, если с него начинается блок — строка «Приложение 1» или заголовок.
    index = ContractIndex(_marked_up(*GOVERNMENT_CONTRACT))
    for clause in (
        "Приложение № 1",
        "Приложение 1",
        "прил. 1",
        "Приложение №1 «Спецификация»",
        "Спецификация (Приложение № 1)",
        "Спецификация",
        "СПЕЦИФИКАЦИЯ",
        # Пункт приложения не сверяется: у спецификации-таблицы номеров нет.
        "п. 3 Приложения № 1",
        "Приложение",
        "8.3",
    ):
        assert index.known_clause(clause), clause
    # Приложение № 2 только упомянуто в тексте пункта, в файле его нет.
    for clause in ("Приложение № 2", "п. 1 Приложения № 2", "Приложение № 3", "Спецификация № 2"):
        assert not index.known_clause(clause), clause


def test_listed_appendix_is_not_attached() -> None:
    # Строка перечня «Приложение № 1 – Спецификация.» приложение не начинает: самого приложения в файле нет.
    index = ContractIndex(_marked_up(*GOVERNMENT_CONTRACT[:5]))
    for clause in ("Приложение № 1", "Спецификация", "п. 3 Приложения № 1"):
        assert not index.known_clause(clause), clause


def test_appendix_names_from_headings_and_list() -> None:
    # Заголовок «СПЕЦИФИКАЦИЯ» без строки «Приложение 1»: Приложение № 1 известно по перечню в п. 8.3.
    without_header = [p for p in GOVERNMENT_CONTRACT if p.text not in ("Приложение 1", "к контракту")]
    index = ContractIndex(_marked_up(*without_header))
    assert index.known_clause("Приложение № 1") and index.known_clause("Спецификация")
    # Заголовок приложения с номером договора и строка «Спецификация № 2 к договору …» в ячейке.
    index = ContractIndex(
        _marked_up(
            RawParagraph("1.1. Товар поставляется по спецификациям."),
            RawParagraph("ПРИЛОЖЕНИЕ № 3 К ДОГОВОРУ № 12/2026 ОТ 01.02.2026"),
            RawParagraph("Спецификация № 2 к договору поставки", table=(0, 0, 0)),
        )
    )
    assert index.known_clause("Приложение № 3") and index.known_clause("Спецификация № 2")
    # «Спецификация» без номера — любая спецификация из файла; другой номер — нет.
    assert index.known_clause("Спецификация") and index.known_clause("Приложение")
    assert not index.known_clause("Приложение № 1") and not index.known_clause("Спецификация № 1")
    # Начало абзаца «Приложением к договору…» и «Приложения:» — не название приложения.
    index = ContractIndex(
        _marked_up(RawParagraph("Приложением к договору является спецификация"), RawParagraph("Приложения:"))
    )
    assert index.documents == set()


def test_check_answer_with_appendices() -> None:
    blocks = _marked_up(*GOVERNMENT_CONTRACT)
    data = answer(
        risks=[risk("R1", clauses=["8.3", "Приложение № 1", "Приложение № 2"], block_ids=[], quote="")],
        contradictions=[
            {"description": "две суммы НДС", "clauses": ["Приложение №1 «Спецификация»"], "block_ids": []}
        ],
        obligations=[],
        favorable=[],
    )
    checked = check_answer(AnalysisAnswer.model_validate(data), blocks)
    assert checked.risk_checks["R1"]["unknown_clauses"] == ["Приложение № 2"]
    assert "R1: в договоре нет пунктов Приложение № 2." in checked.warnings
    assert checked.refs["contradictions"][0]["unknown_clauses"] == []


def test_quote_search() -> None:
    index = ContractIndex(BLOCKS)
    quote = "Поставщик уплачивает неустойку в размере 0,5% от стоимости товара"
    assert index.locate(quote, ["B0002"]) == ("exact", ["B0002"])
    # Модель сослалась не на тот блок: цитата всё равно находится, и блок исправляется.
    assert index.locate(quote, ["B0003"]) == ("exact", ["B0002"])
    # Ошибка распознавания в слове — нечёткое совпадение.
    assert index.locate("Поставщик уплачиваот неустойку в размерe 0,5% от стоимости товара", [])[0] == "fuzzy"
    assert index.locate("Покупатель возмещает упущенную выгоду в полном объёме", []) == ("not_found", [])
    assert index.locate("", ["B0002"]) == ("empty", [])
    # Короткие цитаты нечётко не ищутся.
    assert index.locate("0,7%", [])[0] == "not_found"
    spanning = "не более 20 %. 9.8. Покупатель вправе удержать"
    assert index.locate(spanning, []) == ("exact", ["B0002", "B0003"])
    assert index.known_clause("9.2") and index.known_clause("п. 9") and not index.known_clause("9.9")
    assert index.pages(["B0002", "B0003", "B9999"]) == [3, 4]


def test_check_answer() -> None:
    data = answer(
        top_risks=["R2", "R1", "R2", "R9", "R3"],
        risks=[
            risk("R1", block_ids=["B0002", "B7777"]),
            risk("R2", quote="Покупатель удерживает любые суммы без согласия", clauses=["9.8", "15.1"]),
            risk("R3", is_new_clause=True, quote="", clauses=["9.9"], block_ids=[]),
        ],
        favorable=[
            {"description": "x", "clauses": ["2.4"], "block_ids": ["B0004"], "quote": "день зачисления"},
            {
                "description": "y",
                "clauses": ["3.3"],
                "block_ids": ["B5555"],
                "quote": "нет такого текста в договоре",
            },
        ],
    )
    checked = check_answer(AnalysisAnswer.model_validate(data), BLOCKS)
    r1, r2, r3 = (checked.risk_checks[key] for key in ("R1", "R2", "R3"))
    assert r1["quote_status"] == "exact" and r1["pages"] == [3] and r1["unknown_block_ids"] == ["B7777"]
    assert r2["quote_status"] == "not_found" and r2["unknown_clauses"] == ["15.1"]
    assert r3["quote_status"] == "new_clause" and r3["unknown_clauses"] == []
    assert checked.answer["top_risks"] == ["R2", "R1", "R3"]
    favorable = checked.refs["favorable"]
    assert favorable[0]["quote_status"] == "exact" and favorable[0]["pages"] == [1]
    assert favorable[1]["quote_status"] == "not_found" and favorable[1]["unknown_block_ids"] == ["B5555"]
    assert favorable[1]["unknown_clauses"] == ["3.3"]
    assert checked.refs["obligations"][0]["pages"] == [1]
    warnings = "\n".join(checked.warnings)
    assert "R2: цитата не найдена" in warnings
    assert "R2: в договоре нет пунктов 15.1" in warnings
    assert "несуществующие: R9" in warnings
    assert set(checked.to_dict()) == {"answer", "risk_checks", "refs", "warnings"}


# --- переспрос ------------------------------------------------------------------------------------


def test_analysis_succeeds_first_time() -> None:
    provider = FakeProvider(answer())
    outcome = run_analysis(provider, build_request(BLOCKS, CheckInfo()), BLOCKS)
    assert outcome.attempts == 1
    assert outcome.checked.risk_checks["R1"]["quote_status"] == "exact"
    assert outcome.usage.input_tokens == 1000
    assert outcome.model == "fake-model"


def test_schema_error_is_asked_again_once() -> None:
    broken = answer(risks=[risk(severity="critical")])
    provider = FakeProvider(broken, answer())
    outcome = run_analysis(provider, build_request(BLOCKS, CheckInfo()), BLOCKS)
    assert outcome.attempts == 2
    second = provider.requests[1].user
    assert "ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРИНЯТ" in second
    assert "risks.0.severity" in second
    # Из неудачного ответа в переспрос не попадают значения (в них могли быть цитаты договора).
    assert "critical" not in second.split("ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРИНЯТ")[1]
    assert outcome.usage.input_tokens == 2000
    assert outcome.usage.cost_rub == pytest.approx(1.0)


def test_schema_error_twice_fails() -> None:
    broken = answer(risks=[risk(severity="critical")])
    with pytest.raises(ProviderResponseError, match="дважды") as error:
        run_analysis(FakeProvider(broken, broken), build_request(BLOCKS, CheckInfo()), BLOCKS)
    assert "risks.0.severity" in (error.value.detail or "")


def test_not_json_is_asked_again_but_truncation_is_not() -> None:
    not_json = ProviderResponseError("не JSON", feedback="Верни JSON")
    provider = FakeProvider(not_json, answer())
    assert run_analysis(provider, build_request(BLOCKS, CheckInfo()), BLOCKS).attempts == 2
    assert "Верни JSON" in provider.requests[1].user
    truncated = ProviderResponseError("обрезан")
    with pytest.raises(ProviderResponseError, match="обрезан"):
        run_analysis(FakeProvider(truncated, answer()), build_request(BLOCKS, CheckInfo()), BLOCKS)


# --- оценка ----------------------------------------------------------------------------------------


def test_evaluation_rules() -> None:
    expected = ExpectedProtocol.model_validate(
        {
            "name": "тест",
            "rows": [
                {"n": 1, "clauses": ["6.1", "6.3"], "mandatory": True},
                {"n": 2, "clauses": ["9.9"], "new_clause": True, "mandatory": True},
                {"n": 3, "clauses": ["14.5"]},
            ],
        }
    )
    data = answer(
        risks=[
            risk("R1", clauses=["6.1.2"]),
            risk("R2", clauses=["9.12"], is_new_clause=True, quote="", block_ids=[]),
            risk("R3", clauses=["4.4"]),
        ]
    )
    result = evaluate(expected, data)
    assert result.found_rows == [1, 2]
    assert result.missed_rows == [3]
    assert result.mandatory_missed == []
    assert result.extra_risks == ["R3"]
    assert result.recall == pytest.approx(2 / 3)
    assert result.criteria == {
        "recall_80": False,
        "all_mandatory": True,
        "extra_at_most_one_per_three": False,
    }
    # Новый пункт в другом разделе — не то же самое.
    other = evaluate(expected, answer(risks=[risk("R1", clauses=["12.1"], is_new_clause=True, quote="")]))
    assert 2 in other.missed_rows


def test_evaluation_counts_contradictions_and_missing() -> None:
    expected = ExpectedProtocol.model_validate(
        {
            "name": "тест",
            "rows": [
                {"n": 1, "clauses": ["9.2"], "mandatory": True},
                {"n": 2, "clauses": ["Приложение № 1"]},
                {
                    "n": 3,
                    "clauses": ["Приложение № 1"],
                    "contradictions": False,
                    "missing_keywords": ["Паспорт"],
                },
                {"n": 4, "clauses": ["9.9"], "new_clause": True},
                {"n": 5, "clauses": ["2.4"], "contradictions": False},
            ],
        }
    )
    data = answer(
        risks=[risk("R1", clauses=["9.2"]), risk("R2", clauses=["7.1"])],
        contradictions=[
            {"description": "две суммы НДС", "clauses": ["2.2", "прил. 1"], "block_ids": []},
            {"description": "срок поставки", "clauses": ["9.9"], "block_ids": []},
            {"description": "день оплаты", "clauses": ["2.4"], "block_ids": []},
        ],
        missing=[
            # Слово только в причине не считается: там упоминают и другие документы.
            {"item": "Комплектация товара", "reason": "в паспорте её нет"},
            {"item": "Технический паспорт изделия", "reason": "на него ссылается п. 4.1"},
        ],
    )
    result = evaluate(expected, data)
    assert result.matches == {1: ["R1"], 2: ["contradictions[0]"], 3: ["missing[1]"]}
    assert result.found_rows == [1, 2, 3]
    assert result.found_outside_risks == [2, 3]
    # Строку о новом пункте противоречие не закрывает; строка 5 противоречия не принимает.
    assert result.missed_rows == [4, 5]
    # Лишними считаются только риски.
    assert result.extra_risks == ["R2"] and result.risks_total == 2
    shown = result.to_dict()
    assert shown["matches"] == {"1": ["R1"], "2": ["contradictions[0]"], "3": ["missing[1]"]}
    assert shown["found_outside_risks"] == [2, 3]
    # Риск по тому же пункту важнее: строка найдена в рисках.
    with_risk = evaluate(expected, {**data, "risks": [risk("R1", clauses=["Приложение 1"])]})
    assert with_risk.matches[2] == ["R1", "contradictions[0]"] and 2 not in with_risk.found_outside_risks
    with pytest.raises(ValidationError, match="missing_keywords"):
        ExpectedProtocol.model_validate(
            {"name": "x", "rows": [{"n": 1, "clauses": ["1.1"], "missing_keywords": [" "]}]}
        )
