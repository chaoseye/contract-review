"""Подключение через API, совместимый с OpenAI: RouterAI, OpenRouter, OpenAI, Ollama, vLLM.

Формат ответа:
- json_schema — structured outputs: ответ строго по схеме;
- json_object — JSON-режим: схема добавляется в инструкцию, соблюдение проверяет анализ;
- none — без response_format, только инструкция.
В режиме auto берётся лучший режим, который поддерживает модель. Если провайдер отклоняет запрос
с response_format (400), система один раз переходит к следующему режиму.

Повторы при сетевых ошибках, 429 и 5xx делает SDK (max_retries), лимит времени — timeout_seconds.
"""

import copy
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2
import openai
from openai import OpenAI

from app.analysis.providers.base import (
    PING_REQUEST,
    AnalysisRequest,
    AnalysisResult,
    CheckReport,
    ModelInfo,
    ProfileParams,
    ProviderConfigError,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    Usage,
    parse_json_object,
    schema_instruction,
)
from app.core.db import utcnow

MODES = ("json_schema", "json_object", "none")


def _host(base_url: str) -> str:
    return (urlsplit(base_url).hostname or "").lower()


def _host_is(base_url: str, domain: str) -> bool:
    host = _host(base_url)
    return host == domain or host.endswith("." + domain)


def price_currency(base_url: str) -> str | None:
    """Валюта цен в списке моделей: RouterAI — рубли за токен, OpenRouter — доллары за токен."""
    if _host_is(base_url, "routerai.ru"):
        return "RUB"
    if _host_is(base_url, "openrouter.ai"):
        return "USD"
    return None


def supports_provider_routing(base_url: str) -> bool:
    """Выбор провайдера (поле provider) и список провайдеров модели есть у RouterAI и OpenRouter."""
    return _host_is(base_url, "routerai.ru") or _host_is(base_url, "openrouter.ai")


def supports_country_filter(base_url: str) -> bool:
    """provider.country (только серверы в заданной стране) документирован у RouterAI."""
    return _host_is(base_url, "routerai.ru")


def is_openai(base_url: str) -> bool:
    """Прямое подключение к OpenAI API (не через RouterAI или OpenRouter)."""
    return _host(base_url) == "api.openai.com"


# Модели OpenAI, которые не отвечают текстом через Chat Completions: эмбеддинги, звук, картинки, видео,
# модерация, старые модели дополнения текста; *-pro и *codex* работают только через Responses API.
_OPENAI_NOT_CHAT = re.compile(
    r"embedding|moderation|whisper|tts|transcribe|audio|realtime|dall-e|image|sora|search|"
    r"computer-use|davinci|babbage|instruct|codex|-pro\b"
)


def is_openai_chat_model(model_id: str) -> bool:
    return not _OPENAI_NOT_CHAT.search(model_id.lower())


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # OpenRouter пишет -1, когда цена зависит от запроса.
    return number if number >= 0 else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def model_info_from_dict(data: dict[str, Any], currency: str | None) -> ModelInfo:
    pricing = data.get("pricing") or {}
    prices = {
        "price_input": _number(pricing.get("prompt")),
        "price_output": _number(pricing.get("completion")),
        "price_cache_read": _number(pricing.get("input_cache_read")),
    }
    supported = data.get("supported_parameters")
    return ModelInfo(
        id=str(data.get("id", "")),
        name=str(data.get("name") or ""),
        context_length=_int(data.get("context_length")),
        currency=currency if any(value is not None for value in prices.values()) else None,
        supported_parameters=[str(item) for item in supported] if isinstance(supported, list) else None,
        **prices,
    )


def _is_text_model(data: dict[str, Any]) -> bool:
    outputs = (data.get("architecture") or {}).get("output_modalities")
    return not isinstance(outputs, list) or "text" in outputs


def _error_detail(exc: openai.APIStatusError) -> str | None:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            return str(message) if message else None
        return str(error)
    return str(body) if body else None


class OpenAICompatProvider:
    kind = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        params: ProfileParams,
        http_client: httpx2.Client | None = None,
    ) -> None:
        if not base_url:
            raise ProviderConfigError("Не задан адрес API подключения.")
        if not model:
            raise ProviderConfigError("В подключении не выбрана модель.")
        if params.russia_only and not supports_country_filter(base_url):
            raise ProviderConfigError("«Только серверы в РФ» работает только с RouterAI.")
        self.base_url = base_url
        self.model = model
        self.params = params
        self.currency = price_currency(base_url)
        # Ключ не нужен локальным моделям (Ollama), но SDK требует непустое значение.
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "no-key",
            timeout=params.timeout_seconds,
            max_retries=params.max_retries,
            http_client=http_client,
        )

    # --- анализ ---------------------------------------------------------------------------

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        modes = self._modes()
        started = time.monotonic()
        for index, mode in enumerate(modes):
            try:
                response = self._client.chat.completions.create(**self._request_kwargs(request, mode))
            except openai.BadRequestError as exc:
                # Провайдер не принял response_format: пробуем следующий режим (только в режиме auto).
                if index + 1 < len(modes):
                    continue
                raise self._map_error(exc) from exc
            except openai.OpenAIError as exc:
                raise self._map_error(exc) from exc
            data = self._parse(response)
            return AnalysisResult(
                data=data,
                usage=self._usage(response),
                model=str(getattr(response, "model", "") or self.model),
                response_format=mode,
                duration_seconds=time.monotonic() - started,
            )
        raise AssertionError("недостижимо: список режимов не пуст")  # pragma: no cover

    def _model_info(self) -> ModelInfo | None:
        info = self.params.model_info
        return info if info is not None and info.id == self.model else None

    def _modes(self) -> list[str]:
        if self.params.response_format != "auto":
            return [self.params.response_format]
        info = self._model_info()
        if info is None or info.supported_parameters is None:
            return list(MODES)
        if info.supports("structured_outputs"):
            return list(MODES)
        if info.supports("response_format"):
            return ["json_object", "none"]
        return ["none"]

    def _request_kwargs(self, request: AnalysisRequest, mode: str) -> dict[str, Any]:
        system = (
            request.system
            if mode == "json_schema"
            else f"{request.system}\n\n{schema_instruction(request.schema)}"
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": request.user}],
        }
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            }
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.params.temperature is not None:
            kwargs["temperature"] = self.params.temperature
        if self.params.max_output_tokens is not None:
            info = self._model_info()
            # У OpenAI max_tokens устарел, а модели с рассуждениями (o-серия, GPT-5) его не принимают.
            only_new_name = is_openai(self.base_url) or (
                info is not None
                and info.supports("max_completion_tokens")
                and not info.supports("max_tokens")
            )
            kwargs["max_completion_tokens" if only_new_name else "max_tokens"] = self.params.max_output_tokens
        extra = copy.deepcopy(self.params.extra_body)
        if self.params.russia_only:
            provider = extra.get("provider")
            extra["provider"] = {**(provider if isinstance(provider, dict) else {}), "country": "ru"}
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    def _parse(self, response: Any) -> dict[str, Any]:
        if not response.choices:
            raise ProviderResponseError("Модель вернула пустой ответ.")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ProviderResponseError(
                "Ответ модели обрезан по лимиту длины. Увеличьте «max_output_tokens» в подключении "
                "или выберите модель с большим лимитом ответа."
            )
        if choice.finish_reason == "content_filter":
            raise ProviderResponseError("Провайдер остановил ответ фильтром содержимого.")
        message = choice.message
        if getattr(message, "refusal", None):
            raise ProviderResponseError("Модель отказалась отвечать на запрос.")
        return parse_json_object(message.content or "")

    def _usage(self, response: Any) -> Usage:
        raw = getattr(response, "usage", None)
        if raw is None:
            return Usage()
        details = getattr(raw, "prompt_tokens_details", None)
        usage = Usage(
            input_tokens=raw.prompt_tokens,
            cached_input_tokens=getattr(details, "cached_tokens", None) if details is not None else None,
            output_tokens=raw.completion_tokens,
        )
        reported = _number((getattr(raw, "model_extra", None) or {}).get("cost"))
        if self.currency == "RUB" and reported is not None:
            usage.cost_rub = reported
            return usage
        info = self._model_info()
        if (
            self.currency == "RUB"
            and info is not None
            and info.price_input is not None
            and info.price_output is not None
            and usage.input_tokens is not None
            and usage.output_tokens is not None
        ):
            cached = usage.cached_input_tokens or 0
            cache_price = info.price_cache_read if info.price_cache_read is not None else info.price_input
            usage.cost_rub = (
                (usage.input_tokens - cached) * info.price_input
                + cached * cache_price
                + usage.output_tokens * info.price_output
            )
            usage.cost_estimated = True
        return usage

    # --- модели и проверка связи -----------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """Текстовые модели провайдера с ценами (если провайдер их сообщает)."""
        try:
            page = self._client.models.list()
            items = [model.to_dict() for model in page]
        except openai.OpenAIError as exc:
            raise self._map_error(exc, listing=True) from exc
        models = [model_info_from_dict(item, self.currency) for item in items if _is_text_model(item)]
        if is_openai(self.base_url):
            # OpenAI не сообщает, что умеет модель: отсеиваем по названию те, что не подходят для анализа.
            models = [info for info in models if is_openai_chat_model(info.id)]
        return sorted(models, key=lambda info: info.id)

    def endpoints(self, model_id: str) -> list[dict[str, Any]]:
        """Провайдеры модели у RouterAI и OpenRouter: tag, countries, pricing, supported_parameters."""
        try:
            body = self._client.get(f"/models/{quote(model_id, safe='/~:@')}/endpoints", cast_to=object)
        except openai.OpenAIError as exc:
            raise self._map_error(exc, listing=True) from exc
        data = body.get("data", body) if isinstance(body, dict) else {}
        endpoints = data.get("endpoints") if isinstance(data, dict) else None
        return [item for item in endpoints or [] if isinstance(item, dict)]

    def describe_model(self, models: list[ModelInfo] | None = None) -> ModelInfo:
        """Сведения о выбранной модели. При «Только серверы в РФ» — цены российского провайдера."""
        models = self.list_models() if models is None else models
        info = next((item for item in models if item.id == self.model), None)
        if info is None:
            raise ProviderConfigError(f"Модели «{self.model}» нет в списке моделей по этому адресу.")
        if self.params.russia_only:
            russian = [
                item
                for item in self.endpoints(self.model)
                if "ru" in (item.get("countries") or []) and (_int(item.get("status")) or 0) >= 0
            ]
            if not russian:
                raise ProviderConfigError(
                    f"У модели «{self.model}» нет провайдеров с серверами в РФ. Выберите другую модель "
                    "или снимите «Только серверы в РФ»."
                )
            cheapest = min(
                (model_info_from_dict({"id": self.model, **item}, self.currency) for item in russian),
                key=lambda item: item.price_input if item.price_input is not None else float("inf"),
            )
            info = info.model_copy(
                update={
                    "price_input": cheapest.price_input,
                    "price_output": cheapest.price_output,
                    "price_cache_read": cheapest.price_cache_read,
                    "currency": cheapest.currency,
                    "supported_parameters": cheapest.supported_parameters or info.supported_parameters,
                    "countries": ["ru"],
                }
            )
        return info.model_copy(update={"checked_at": utcnow()})

    def check(self) -> CheckReport:
        """Проверка связи: модель есть в списке, ключ и баланс в порядке, ответ по схеме приходит."""
        notes: list[str] = []
        info: ModelInfo | None = None
        if supports_provider_routing(self.base_url):
            info = self.describe_model()
        else:
            # У локальных серверов и других совместимых API списка моделей может не быть.
            try:
                models = self.list_models()
            except ProviderError as exc:
                notes.append(f"Список моделей не получен: {exc.message}")
            else:
                info = self.describe_model(models)
        if info is not None:
            self.params = self.params.model_copy(update={"model_info": info})
        result = self.analyze(PING_REQUEST)
        if result.data.get("ok") is not True:
            notes.append("Модель ответила, но не по схеме проверки: качество ответов стоит проверить.")
        return CheckReport(model_info=info, result=result, notes=notes)

    # --- ошибки ----------------------------------------------------------------------------

    def _map_error(self, exc: openai.OpenAIError, listing: bool = False) -> ProviderError:
        timeout = self.params.timeout_seconds
        if isinstance(exc, openai.APITimeoutError):
            return ProviderUnavailableError(
                f"Модель не ответила за {timeout} с. "
                "Увеличьте лимит времени в подключении или повторите позже."
            )
        if isinstance(exc, openai.APIConnectionError):
            return ProviderUnavailableError(
                "Нет связи с провайдером ИИ: проверьте адрес API и доступ сервера в интернет."
            )
        if isinstance(exc, openai.APIResponseValidationError):
            return ProviderResponseError("Провайдер прислал ответ в неожиданном формате.")
        if not isinstance(exc, openai.APIStatusError):
            return ProviderUnavailableError(f"Ошибка подключения к провайдеру ИИ ({type(exc).__name__}).")
        status = exc.status_code
        detail = _error_detail(exc)
        if status == 401:
            return ProviderConfigError("Ключ API не принят (401). Проверьте ключ в подключении.", detail)
        if status == 402:
            return ProviderConfigError("Недостаточно средств на балансе провайдера ИИ (402).", detail)
        if status == 403:
            return ProviderConfigError(
                "Доступ запрещён (403): ключ не даёт доступа к этой модели или из этого региона.", detail
            )
        if status == 404:
            if listing:
                return ProviderConfigError(
                    "По этому адресу нет списка моделей (404): проверьте адрес API.", detail
                )
            russia = " или у неё нет провайдеров с серверами в РФ" if self.params.russia_only else ""
            return ProviderConfigError(
                f"Модель «{self.model}» не найдена у провайдера (404){russia}.", detail
            )
        if status in (400, 413, 422):
            return ProviderConfigError(f"Провайдер отклонил запрос ({status}).", detail)
        if status == 429:
            return ProviderUnavailableError(
                "Провайдер ИИ ограничил частоту запросов (429). Повторите позже.", detail
            )
        if status >= 500 or status in (408, 409):
            return ProviderUnavailableError(
                f"Ошибка на стороне провайдера ИИ ({status}). Повторите позже.", detail
            )
        return ProviderConfigError(f"Провайдер ИИ вернул ошибку {status}.", detail)
