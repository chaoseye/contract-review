"""Общий интерфейс подключений ИИ (documentation/ARCHITECTURE.md, раздел 5).

Подключение получает промпт и JSON Schema ответа и возвращает разобранный JSON-объект, токены и стоимость.
Проверку ответа по схеме и цитат делает анализ, а не подключение.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ResponseFormat = Literal["auto", "json_schema", "json_object", "none"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelInfo(BaseModel):
    """Сведения о модели из списка провайдера. Цены — за один токен в валюте провайдера."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    context_length: int | None = None
    # Наибольшая длина ответа в токенах (Anthropic сообщает её в списке моделей); None — неизвестно.
    max_output_tokens: int | None = None
    # RouterAI считает в рублях, OpenRouter — в долларах; у остальных цен в списке моделей нет.
    currency: Literal["RUB", "USD"] | None = None
    price_input: float | None = None
    price_output: float | None = None
    price_cache_read: float | None = None
    # Параметры запроса, которые поддерживает модель (RouterAI, OpenRouter), или возможности модели
    # у Anthropic (structured_outputs, effort, effort_high, thinking_adaptive…); None — неизвестно.
    supported_parameters: list[str] | None = None
    # Страны серверов провайдеров, через которые пойдёт запрос; None — не запрашивались.
    countries: list[str] | None = None
    checked_at: datetime | None = None

    def supports(self, parameter: str) -> bool | None:
        if self.supported_parameters is None:
            return None
        return parameter in self.supported_parameters


class ProfileParams(BaseModel):
    """Настройки подключения, которые хранятся в llm_profiles.params."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    # Повторы SDK при сетевых ошибках, 429 и 5xx (с паузой, которая растёт).
    max_retries: int = Field(default=2, ge=0, le=5)
    # auto: схема (structured outputs), если модель её поддерживает, иначе JSON-режим, иначе инструкция.
    response_format: ResponseFormat = "auto"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=256, le=200_000)
    # RouterAI: запросы только к провайдерам с серверами в РФ (provider.country = "ru").
    russia_only: bool = False
    # Anthropic: глубина рассуждений модели (output_config.effort); None — по умолчанию модели.
    effort: Effort | None = None
    # Дополнительные поля тела запроса, например {"reasoning": {"effort": "high"}}.
    extra_body: dict[str, Any] = Field(default_factory=dict)
    # Codex CLI: значения --config key=value, например model_reasoning_effort="high".
    codex_config: list[str] = Field(default_factory=list)
    # Последние 4 символа ключа — для показа без расшифровки.
    key_hint: str = ""
    # Снимок сведений о модели с последней проверки связи: цены и поддерживаемые параметры.
    model_info: ModelInfo | None = None


@dataclass(frozen=True)
class AnalysisRequest:
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str = "analysis"


@dataclass
class Usage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cost_rub: float | None = None
    # True — стоимость посчитана по ценам из списка моделей, а не получена от провайдера.
    cost_estimated: bool = False


@dataclass
class AnalysisResult:
    data: dict[str, Any]
    usage: Usage
    model: str
    # Как получен JSON: json_schema, json_object, none (по инструкции) или codex.
    response_format: str
    duration_seconds: float


@dataclass
class CheckReport:
    """Итог проверки связи для администратора."""

    model_info: ModelInfo | None
    result: AnalysisResult
    notes: list[str] = field(default_factory=list)


class ProviderError(Exception):
    """Ошибка подключения.

    message — для администратора и менеджера, без текста договора. detail — ответ провайдера как есть:
    показывается администратору при проверке связи и хранится в проверке, в логи не пишется.
    """

    retryable = False

    def __init__(
        self,
        message: str,
        detail: str | None = None,
        *,
        retryable: bool | None = None,
        feedback: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail[:1000] if detail else None
        if retryable is not None:
            self.retryable = retryable
        # Что сказать модели, если имеет смысл переспросить её тем же запросом (ответ не JSON).
        self.feedback = feedback


class ProviderConfigError(ProviderError):
    """Неверные настройки: ключ, модель, баланс, нет входа. Повтор не поможет."""


class ProviderUnavailableError(ProviderError):
    """Сеть, перегрузка провайдера, ошибка на его стороне, таймаут. Имеет смысл повторить позже."""

    retryable = True


class ProviderResponseError(ProviderError):
    """Ответ получен, но не годится: не JSON, обрезан по длине, отказ модели."""


class Provider(Protocol):
    model: str

    def analyze(self, request: AnalysisRequest) -> AnalysisResult: ...

    def check(self) -> CheckReport: ...


# Короткий запрос для проверки связи: без договора, ответ по маленькой схеме.
PING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "answer": {"type": "string"}},
    "required": ["ok", "answer"],
    "additionalProperties": False,
}
PING_REQUEST = AnalysisRequest(
    system="Это проверка связи. Ответь по схеме: ok — true, answer — слово «готов».",
    user="Проверка связи.",
    schema=PING_SCHEMA,
    schema_name="ping",
)


def schema_instruction(schema: dict[str, Any]) -> str:
    """Текст для режимов без structured outputs: модель должна сама соблюсти схему."""
    return (
        "Ответь одним JSON-объектом без пояснений и без markdown. Он должен соответствовать JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Разобрать ответ модели как JSON-объект. Терпит обёртку ```json и текст вокруг объекта."""
    candidates = [text.strip()]
    candidates += [match.strip() for match in _FENCE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    # Сам ответ в сообщение не попадает: в нём могут быть цитаты договора.
    raise ProviderResponseError(
        f"Ответ модели не удалось разобрать как JSON-объект ({len(text)} символов).",
        feedback="Ответ не был JSON-объектом. Верни только JSON-объект по схеме, без текста вокруг.",
    )
