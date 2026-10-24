"""Общее для тестов анализа: поддельный провайдер и ответ модели, который проходит схему."""

import copy
from typing import Any

from app.analysis.providers.base import AnalysisRequest, AnalysisResult, CheckReport, Usage

BLOCKS: list[dict[str, Any]] = [
    {"id": "B0001", "kind": "heading", "text": "9. ОТВЕТСТВЕННОСТЬ СТОРОН", "clause": "9", "page": 3},
    {
        "id": "B0002",
        "kind": "paragraph",
        "text": "9.2. В случае просрочки поставки Поставщик уплачивает неустойку в размере 0,5 % "
        "от стоимости "
        "товара за каждый день просрочки, но не более 20 %.",
        "clause": "9.2",
        "page": 3,
    },
    {
        "id": "B0003",
        "kind": "paragraph",
        "text": "9.8. Покупатель вправе удержать сумму неустойки из оплаты.",
        "clause": "9.8",
        "page": 4,
    },
    {
        "id": "B0004",
        "kind": "paragraph",
        "text": "2.4. Оплата считается произведённой в день зачисления.",
        "clause": "2.4",
        "page": 1,
    },
]


def risk(risk_id: str = "R1", **fields: Any) -> dict[str, Any]:
    data = {
        "id": risk_id,
        "clauses": ["9.2"],
        "block_ids": ["B0002"],
        "is_new_clause": False,
        "quote": "Поставщик уплачивает неустойку в размере 0,5% от стоимости товара за каждый день просрочки",
        "title": "Высокая неустойка за просрочку поставки",
        "explanation": "За месяц просрочки — 15% стоимости товара.",
        "severity": "high",
        "stop_factor": True,
        "category": "ответственность",
        "recommendation": "Снизить до 0,1% в день, не более 10%.",
        "proposed_wording": "9.2. При нарушении срока поставки Поставщик уплачивает неустойку 0,1% в день, "
        "не более 10%.",
    }
    data.update(fields)
    return data


def answer(**fields: Any) -> dict[str, Any]:
    data = {
        "contract": {
            "kind": "поставки",
            "number": "12/2026",
            "date": "2026-07-31",
            "city": None,
            "parties_order": ["buyer", "supplier"],
            "buyer": {"name": "АО «Пример»", "representative": None, "basis": None},
            "our_role": "supplier",
            "procurement": "commercial",
        },
        "summary": "Договор можно подписывать после правок неустойки и удержания.",
        "approval": {"allowed": False, "reasons": ["неустойка без разумного предела"]},
        "confidence": {"level": "medium", "reasons": []},
        "top_risks": ["R1"],
        "risks": [risk()],
        "obligations": [
            {
                "party": "buyer",
                "obligation": "оплатить товар",
                "deadline": None,
                "clauses": ["2.4"],
                "block_ids": ["B0004"],
            }
        ],
        "contradictions": [],
        "missing": [{"item": "Спецификация", "reason": "на неё ссылается договор"}],
        "favorable": [
            {
                "description": "оплата по зачислению",
                "clauses": ["2.4"],
                "block_ids": ["B0004"],
                "quote": "Оплата считается произведённой в день зачисления",
            }
        ],
        "specialist_checks": [{"specialist": "lawyer", "what": "полномочия подписанта"}],
    }
    data.update(copy.deepcopy(fields))
    return data


class FakeProvider:
    """Подключение-подделка: отдаёт ответы по очереди и запоминает запросы."""

    kind = "fake"

    def __init__(self, *responses: dict[str, Any] | Exception, model: str = "fake-model") -> None:
        self.responses = list(responses)
        self.requests: list[AnalysisRequest] = []
        self.model = model

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return AnalysisResult(
            data=copy.deepcopy(item),
            usage=Usage(
                input_tokens=1000,
                cached_input_tokens=100,
                output_tokens=200,
                cost_rub=0.5,
                cost_estimated=True,
            ),
            model=self.model,
            response_format="json_schema",
            duration_seconds=0.01,
        )

    def check(self) -> CheckReport:  # pragma: no cover — в тестах анализа не нужен
        raise NotImplementedError
