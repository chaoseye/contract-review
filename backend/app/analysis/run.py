"""Анализ договора целиком: запрос к модели, проверка схемы с одним переспросом, проверка по договору.

Используется задачей analyze в worker и командами `python -m app.cli analyze|evaluate`.
"""

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.analysis.prompt import with_feedback
from app.analysis.providers.base import AnalysisRequest, Provider, ProviderResponseError, Usage
from app.analysis.schema import AnalysisAnswer
from app.analysis.validate import CheckedAnswer, check_answer

MAX_ATTEMPTS = 2


@dataclass
class AnalysisOutcome:
    checked: CheckedAnswer
    usage: Usage
    model: str
    response_format: str
    attempts: int
    duration_seconds: float


def _add_usage(total: Usage | None, part: Usage) -> Usage:
    if total is None:
        return Usage(**vars(part))

    def plus(left: int | None, right: int | None) -> int | None:
        return None if left is None and right is None else (left or 0) + (right or 0)

    return Usage(
        input_tokens=plus(total.input_tokens, part.input_tokens),
        cached_input_tokens=plus(total.cached_input_tokens, part.cached_input_tokens),
        output_tokens=plus(total.output_tokens, part.output_tokens),
        cost_rub=None if total.cost_rub is None or part.cost_rub is None else total.cost_rub + part.cost_rub,
        cost_estimated=total.cost_estimated or part.cost_estimated,
    )


def validation_feedback(exc: ValidationError, limit: int = 20) -> str:
    """Ошибки схемы для модели: где и что не так. Значения из ответа не повторяются (в них цитаты)."""
    lines = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(part) for part in error["loc"]) or "ответ"
        lines.append(f"- {location}: {error['msg']}")
    if exc.error_count() > limit:
        lines.append(f"- … и ещё {exc.error_count() - limit} ошибок")
    return "Ответ не соответствует схеме:\n" + "\n".join(lines)


def run_analysis(
    provider: Provider, request: AnalysisRequest, blocks: Iterable[Mapping[str, Any]]
) -> AnalysisOutcome:
    """Запросить модель и проверить ответ. Ответ не по схеме или не JSON переспрашивается один раз.

    Ошибки подключения (ProviderError) не перехватываются: что с ними делать, решает вызывающий.
    """
    blocks = list(blocks)
    started = time.monotonic()
    usage: Usage | None = None
    current = request
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = provider.analyze(current)
        except ProviderResponseError as exc:
            if exc.feedback is None or attempt == MAX_ATTEMPTS:
                raise
            current = with_feedback(request, exc.feedback)
            continue
        usage = _add_usage(usage, result.usage)
        try:
            answer = AnalysisAnswer.model_validate(result.data)
        except ValidationError as exc:
            feedback = validation_feedback(exc)
            if attempt == MAX_ATTEMPTS:
                raise ProviderResponseError(
                    "Ответ модели дважды не прошёл проверку схемы. "
                    "Попробуйте другую модель или повторите анализ.",
                    feedback,
                ) from exc
            current = with_feedback(request, feedback)
            continue
        return AnalysisOutcome(
            checked=check_answer(answer, blocks),
            usage=usage,
            model=result.model,
            response_format=result.response_format,
            attempts=attempt,
            duration_seconds=time.monotonic() - started,
        )
    raise AssertionError("недостижимо")  # pragma: no cover
