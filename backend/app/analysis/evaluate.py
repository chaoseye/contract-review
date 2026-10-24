"""Оценка анализа на паре «договор → готовый протокол разногласий к нему».

Ожидаемые строки протокола описываются JSON-файлом:

    {"name": "Договор № 1", "rows": [{"n": 1, "clauses": ["4.1"], "new_clause": false,
                                     "topic": "качество", "mandatory": false}, …]}

Где ответ модели засчитывается строке:
- риск, у которого есть пункт из списка строки (или пункт уровнем выше или ниже: 6.1 и 6.1.2). Новый пункт
  сопоставляется с новым пунктом системы в том же разделе: номер, который предложит модель, может отличаться;
- противоречие (contradictions) с таким же пунктом — кроме строк о новом пункте и строк с
  "contradictions": false;
- недостающий документ (missing), в названии которого (item) есть одно из слов строки "missing_keywords".
  Пунктов у него нет, поэтому без слов строка по недостающему не засчитывается.
Номера приложений сравниваются в одном виде: «Приложение № 1», «прил. 1» и «Приложение №1 «Спецификация»» —
одно и то же (normalize_clause). Лишними считаются только риски, которые не подошли ни к одной строке.
Строки, найденные не в рисках, перечисляются отдельно: строки протокола из них система не соберёт.
Совпадение по смыслу проверяет человек: скрипт его не видит.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.analysis.validate import normalize, normalize_clause


class ExpectedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    clauses: list[str] = Field(min_length=1)
    new_clause: bool = False
    topic: str = ""
    mandatory: bool = False
    # Искать ли строку и в противоречиях. false — если у соседней строки тот же пункт, а противоречие о ней.
    contradictions: bool = True
    # Части слов, по которым строку находит недостающий документ: «паспорт» найдёт «Технический паспорт».
    missing_keywords: list[str] = Field(default_factory=list)

    @field_validator("missing_keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        keywords = [normalize(keyword) for keyword in value]
        if any(not keyword for keyword in keywords):
            raise ValueError("пустое слово в missing_keywords: оно совпало бы с любым документом")
        return keywords


class ExpectedProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rows: list[ExpectedRow] = Field(min_length=1)


def load_expected(path: Path) -> ExpectedProtocol:
    return ExpectedProtocol.model_validate_json(path.read_text(encoding="utf-8"))


def _related(left: str, right: str) -> bool:
    a, b = normalize_clause(left), normalize_clause(right)
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def _section(clause: str) -> str:
    return normalize_clause(clause).split(".")[0]


def _clauses(item: dict[str, Any]) -> list[str]:
    return [str(clause) for clause in item.get("clauses", [])]


def _same_clause(row: ExpectedRow, clauses: list[str]) -> bool:
    return any(_related(expected, actual) for expected in row.clauses for actual in clauses)


def _matches(row: ExpectedRow, risk: dict[str, Any]) -> bool:
    clauses = _clauses(risk)
    if row.new_clause:
        sections = {_section(clause) for clause in row.clauses}
        return bool(risk.get("is_new_clause")) and any(_section(clause) in sections for clause in clauses)
    return _same_clause(row, clauses)


def _matches_contradiction(row: ExpectedRow, item: dict[str, Any]) -> bool:
    # У противоречия нет новой редакции: строку о новом пункте оно не закрывает.
    return row.contradictions and not row.new_clause and _same_clause(row, _clauses(item))


def _matches_missing(row: ExpectedRow, item: dict[str, Any]) -> bool:
    # Только название: в причине упоминаются другие документы и пункты, по ней легко ошибиться.
    name = normalize(str(item.get("item", "")))
    return any(keyword in name for keyword in row.missing_keywords)


def _dicts(answer: dict[str, Any], name: str) -> list[tuple[int, dict[str, Any]]]:
    items = answer.get(name)
    if not isinstance(items, list):
        return []
    return [(index, item) for index, item in enumerate(items) if isinstance(item, dict)]


@dataclass
class Evaluation:
    total_rows: int
    found_rows: list[int]
    missed_rows: list[int]
    mandatory_total: int
    mandatory_missed: list[int]
    extra_risks: list[str]
    risks_total: int
    # Что в ответе закрыло строку: id риска, contradictions[i], missing[i] (индексы — как в ответе модели).
    matches: dict[int, list[str]] = field(default_factory=dict)
    # Строки без риска — найдены только в противоречиях или недостающем: строки протокола для них нет.
    found_outside_risks: list[int] = field(default_factory=list)
    criteria: dict[str, bool] = field(default_factory=dict)

    @property
    def recall(self) -> float:
        return len(self.found_rows) / self.total_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "found_rows": self.found_rows,
            "missed_rows": self.missed_rows,
            "recall": round(self.recall, 3),
            "mandatory_total": self.mandatory_total,
            "mandatory_missed": self.mandatory_missed,
            "risks_total": self.risks_total,
            "extra_risks": self.extra_risks,
            "matches": {str(n): refs for n, refs in self.matches.items()},
            "found_outside_risks": self.found_outside_risks,
            "criteria": self.criteria,
        }


def evaluate(expected: ExpectedProtocol, answer: dict[str, Any]) -> Evaluation:
    """Сравнить ответ модели (поле answer результата анализа) с ожидаемыми строками протокола."""
    risks = [risk for _, risk in _dicts(answer, "risks")]
    contradictions = _dicts(answer, "contradictions")
    missing = _dicts(answer, "missing")
    matches: dict[int, list[str]] = {}
    outside_risks: list[int] = []
    for row in expected.rows:
        by_risk = [str(risk.get("id")) for risk in risks if _matches(row, risk)]
        others = [f"contradictions[{i}]" for i, item in contradictions if _matches_contradiction(row, item)]
        others += [f"missing[{i}]" for i, item in missing if _matches_missing(row, item)]
        if by_risk or others:
            matches[row.n] = by_risk + others
        if others and not by_risk:
            outside_risks.append(row.n)
    found = [row.n for row in expected.rows if row.n in matches]
    missed = [row.n for row in expected.rows if row.n not in matches]
    mandatory = [row for row in expected.rows if row.mandatory]
    extra = [str(risk.get("id")) for risk in risks if not any(_matches(row, risk) for row in expected.rows)]
    result = Evaluation(
        total_rows=len(expected.rows),
        found_rows=found,
        missed_rows=missed,
        mandatory_total=len(mandatory),
        mandatory_missed=[row.n for row in mandatory if row.n in missed],
        extra_risks=extra,
        risks_total=len(risks),
        matches=matches,
        found_outside_risks=outside_risks,
    )
    # Порог: полнота от 80%, все обязательные строки, лишних не больше одного на три найденных.
    result.criteria = {
        "recall_80": result.recall >= 0.8,
        "all_mandatory": not result.mandatory_missed,
        "extra_at_most_one_per_three": len(extra) * 3 <= len(found),
    }
    return result


def dump(evaluation: Evaluation) -> str:
    return json.dumps(evaluation.to_dict(), ensure_ascii=False, indent=2)
