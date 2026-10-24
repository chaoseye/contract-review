"""Подключение к API Anthropic (модели Claude) через официальный SDK anthropic.

- Запрос — Messages API. Промпт системы помечается для кэша промпта: при нескольких проверках подряд
  и при переспросе (раздел 4.4) он не оплачивается заново целиком.
- Формат ответа: json_schema — structured outputs (output_config.format), ответ строго по схеме;
  none — схема в инструкции. В режиме auto берётся схема, если модель её поддерживает. Если API
  отклоняет запрос со схемой (400), система один раз переходит к инструкции. JSON-режима у Anthropic нет.
- Ответ читается потоком: анализ длинный, а поток не упирается в лимит одного HTTP-ответа.
  timeout_seconds — общий лимит времени на ответ.
- Рассуждения: adaptive thinking, если модель его поддерживает; глубина — effort из подключения.
- Если классификаторы безопасности Anthropic отклонят запрос, у моделей из FALLBACK_MODELS API сам
  повторяет его на рекомендованной модели (fallbacks: "default").

Повторы при сетевых ошибках, 429, 5xx и 529 делает SDK (max_retries).
"""

import copy
import time
from typing import Any
from urllib.parse import urlsplit

import anthropic
import httpx2
from anthropic import Anthropic

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

DEFAULT_BASE_URL = "https://api.anthropic.com"
MODES = ("json_schema", "none")
# Лимит длины ответа, если в подключении он не задан: анализ с рассуждениями и новыми редакциями
# пунктов бывает длинным. Если модель умеет меньше, берётся её предел из списка моделей.
DEFAULT_MAX_TOKENS = 64_000
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Модели, у которых есть резервная модель на стороне Anthropic (только API Anthropic, не прокси).
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Ошибки, которые приходят внутри потока (HTTP 200) и означают временную недоступность.
TRANSIENT_STREAM_ERRORS = frozenset({"overloaded_error", "api_error", "rate_limit_error", "timeout_error"})
# Часы для лимита времени на ответ (тесты подменяют их, не трогая time.monotonic).
_now = time.monotonic


def normalize_base_url(base_url: str) -> str:
    """SDK сам добавляет /v1: адрес вида https://api.anthropic.com/v1 приводится к корню."""
    base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


def _is_anthropic_api(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "").lower() == "api.anthropic.com"


def _supported(capability: Any) -> bool:
    return bool(capability is not None and getattr(capability, "supported", False))


def model_info_from_api(model: Any) -> ModelInfo:
    """Сведения о модели из GET /v1/models: контекст, предел ответа и возможности."""
    capabilities = getattr(model, "capabilities", None)
    supported: list[str] | None = None
    if capabilities is not None:
        supported = []
        if _supported(getattr(capabilities, "structured_outputs", None)):
            supported.append("structured_outputs")
        effort = getattr(capabilities, "effort", None)
        if _supported(effort):
            supported.append("effort")
            supported += [
                f"effort_{level}" for level in EFFORT_LEVELS if _supported(getattr(effort, level, None))
            ]
        thinking = getattr(capabilities, "thinking", None)
        types = getattr(thinking, "types", None)
        if _supported(thinking) and types is not None:
            supported += [
                f"thinking_{kind}"
                for kind in ("adaptive", "enabled")
                if _supported(getattr(types, kind, None))
            ]
    return ModelInfo(
        id=str(model.id),
        name=str(getattr(model, "display_name", "") or ""),
        context_length=getattr(model, "max_input_tokens", None),
        max_output_tokens=getattr(model, "max_tokens", None),
        supported_parameters=supported,
    )


def _error_detail(body: Any) -> str | None:
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            return str(message) if message else None
        return str(error)
    return str(body) if body else None


def _error_type(body: Any) -> str | None:
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("type")) if isinstance(error, dict) and error.get("type") else None


class AnthropicProvider:
    kind = "anthropic"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        params: ProfileParams,
        http_client: httpx2.Client | None = None,
    ) -> None:
        if not model:
            raise ProviderConfigError("В подключении не выбрана модель.")
        if not api_key:
            raise ProviderConfigError("В подключении Anthropic не задан ключ API.")
        if params.russia_only:
            raise ProviderConfigError("«Только серверы в РФ» работает только с RouterAI.")
        self.base_url = normalize_base_url(base_url)
        self.model = model
        self.params = params
        self._client = Anthropic(
            base_url=self.base_url,
            api_key=api_key,
            timeout=params.timeout_seconds,
            max_retries=params.max_retries,
            http_client=http_client,
        )

    # --- анализ ---------------------------------------------------------------------------

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        modes = self._modes()
        started = _now()
        for index, mode in enumerate(modes):
            try:
                message = self._stream(self._request_kwargs(request, mode))
            except anthropic.BadRequestError as exc:
                # API не принял схему ответа: пробуем инструкцию (только в режиме auto).
                if index + 1 < len(modes):
                    continue
                raise self._map_error(exc) from exc
            except anthropic.AnthropicError as exc:
                raise self._map_error(exc) from exc
            data = self._parse(message)
            return AnalysisResult(
                data=data,
                usage=self._usage(message),
                model=str(getattr(message, "model", "") or self.model),
                response_format=mode,
                duration_seconds=_now() - started,
            )
        raise AssertionError("недостижимо: список режимов не пуст")  # pragma: no cover

    def _model_info(self) -> ModelInfo | None:
        info = self.params.model_info
        return info if info is not None and info.id == self.model else None

    def _modes(self) -> list[str]:
        if self.params.response_format == "json_object":
            # Проверяется и при сохранении подключения; здесь — на случай старых записей.
            raise ProviderConfigError("У Anthropic нет JSON-режима: выберите «Автоматически» или схему.")
        if self.params.response_format != "auto":
            return [self.params.response_format]
        info = self._model_info()
        if info is not None and info.supports("structured_outputs") is False:
            return ["none"]
        return list(MODES)

    def _effort(self) -> str | None:
        effort = self.params.effort
        info = self._model_info()
        if effort is None or info is None or info.supported_parameters is None:
            return effort
        return effort if info.supports(f"effort_{effort}") else None

    def _thinking(self) -> dict[str, str] | None:
        # Без сведений о модели параметр не отправляется: модель работает в своём режиме по умолчанию.
        info = self._model_info()
        return {"type": "adaptive"} if info is not None and info.supports("thinking_adaptive") else None

    def _max_tokens(self) -> int:
        if self.params.max_output_tokens is not None:
            return self.params.max_output_tokens
        info = self._model_info()
        limit = info.max_output_tokens if info is not None else None
        return min(DEFAULT_MAX_TOKENS, limit) if limit else DEFAULT_MAX_TOKENS

    def _uses_fallbacks(self) -> bool:
        return _is_anthropic_api(self.base_url) and self.model in FALLBACK_MODELS

    def _request_kwargs(self, request: AnalysisRequest, mode: str) -> dict[str, Any]:
        system = (
            request.system
            if mode == "json_schema"
            else f"{request.system}\n\n{schema_instruction(request.schema)}"
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens(),
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": request.user}],
        }
        output_config: dict[str, Any] = {}
        if mode == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        effort = self._effort()
        if effort is not None:
            output_config["effort"] = effort
        if output_config:
            kwargs["output_config"] = output_config
        thinking = self._thinking()
        if thinking is not None:
            kwargs["thinking"] = thinking
        if self._uses_fallbacks():
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        extra = copy.deepcopy(self.params.extra_body)
        if self.params.temperature is not None:
            # В SDK нет параметра temperature: новые модели Claude его не принимают, старые — принимают.
            extra.setdefault("temperature", self.params.temperature)
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    def _stream(self, kwargs: dict[str, Any]) -> Any:
        """Прочитать ответ потоком и вернуть итоговое сообщение. Лимит времени — на весь ответ."""
        timeout = self.params.timeout_seconds
        deadline = _now() + timeout
        api = self._client.beta.messages if "betas" in kwargs else self._client.messages
        with api.stream(**kwargs) as stream:
            for _event in stream:
                if _now() > deadline:
                    raise ProviderUnavailableError(
                        f"Модель не ответила за {timeout} с. "
                        "Увеличьте лимит времени в подключении или повторите позже."
                    )
            return stream.get_final_message()

    def _parse(self, message: Any) -> dict[str, Any]:
        reason = getattr(message, "stop_reason", None)
        if reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            suffix = f" (категория «{category}»)" if category else ""
            raise ProviderResponseError(f"Модель отказалась отвечать на запрос{suffix}.")
        if reason == "max_tokens":
            raise ProviderResponseError(
                "Ответ модели обрезан по лимиту длины. Увеличьте «max_output_tokens» в подключении "
                "или выберите модель с большим лимитом ответа."
            )
        if reason == "model_context_window_exceeded":
            raise ProviderResponseError(
                "Договор вместе с промптом не помещается в контекст модели. "
                "Выберите модель с большим контекстом."
            )
        blocks = list(getattr(message, "content", None) or [])
        # После переключения на резервную модель берётся только её ответ.
        switches = [index for index, block in enumerate(blocks) if getattr(block, "type", "") == "fallback"]
        if switches:
            blocks = blocks[switches[-1] + 1 :]
        text = "".join(block.text for block in blocks if getattr(block, "type", "") == "text")
        if not text.strip():
            raise ProviderResponseError("Модель вернула пустой ответ.")
        return parse_json_object(text)

    def _usage(self, message: Any) -> Usage:
        raw = getattr(message, "usage", None)
        if raw is None:
            return Usage()
        read = getattr(raw, "cache_read_input_tokens", None)
        written = getattr(raw, "cache_creation_input_tokens", None)
        # У Anthropic input_tokens — без кэша; для журнала считаем весь вход, как у остальных подключений.
        return Usage(
            input_tokens=(raw.input_tokens or 0) + (read or 0) + (written or 0),
            cached_input_tokens=read,
            output_tokens=raw.output_tokens,
        )

    # --- модели и проверка связи -----------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """Модели, доступные ключу, — в порядке API: сначала новые."""
        try:
            return [model_info_from_api(model) for model in self._client.models.list(limit=100)]
        except anthropic.AnthropicError as exc:
            raise self._map_error(exc, listing=True) from exc

    def describe_model(self) -> ModelInfo:
        try:
            model = self._client.models.retrieve(self.model)
        except anthropic.NotFoundError as exc:
            raise ProviderConfigError(
                f"Модели «{self.model}» нет у Anthropic или она недоступна этому ключу.",
                _error_detail(exc.body),
            ) from exc
        except anthropic.AnthropicError as exc:
            raise self._map_error(exc, listing=True) from exc
        return model_info_from_api(model).model_copy(update={"checked_at": utcnow()})

    def check(self) -> CheckReport:
        """Проверка связи: модель доступна ключу, ответ по схеме приходит."""
        notes: list[str] = []
        info = self.describe_model()
        self.params = self.params.model_copy(update={"model_info": info})
        if info.supports("structured_outputs") is False:
            notes.append("Модель не поддерживает строгую схему ответа: схема уйдёт в инструкцию.")
        if self.params.effort is not None and self._effort() is None:
            notes.append(
                f"Модель не поддерживает глубину рассуждений «{self.params.effort}»: "
                "параметр не отправляется."
            )
        result = self.analyze(PING_REQUEST)
        if result.data.get("ok") is not True:
            notes.append("Модель ответила, но не по схеме проверки: качество ответов стоит проверить.")
        return CheckReport(model_info=info, result=result, notes=notes)

    # --- ошибки ----------------------------------------------------------------------------

    def _map_error(self, exc: anthropic.AnthropicError, listing: bool = False) -> ProviderError:
        timeout = self.params.timeout_seconds
        if isinstance(exc, anthropic.APITimeoutError):
            return ProviderUnavailableError(
                f"Модель не ответила за {timeout} с. "
                "Увеличьте лимит времени в подключении или повторите позже."
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderUnavailableError(
                "Нет связи с Anthropic: проверьте адрес API и доступ сервера в интернет."
            )
        if isinstance(exc, anthropic.APIResponseValidationError):
            return ProviderResponseError("Anthropic прислал ответ в неожиданном формате.")
        if not isinstance(exc, anthropic.APIStatusError):
            return ProviderUnavailableError(f"Ошибка подключения к Anthropic ({type(exc).__name__}).")
        status = exc.status_code
        detail = _error_detail(exc.body)
        if status == 200:
            # Ошибка пришла внутри потока, после начала ответа.
            if _error_type(exc.body) in TRANSIENT_STREAM_ERRORS:
                return ProviderUnavailableError(
                    "Anthropic прервал ответ из-за перегрузки или сбоя. Повторите позже.", detail
                )
            return ProviderResponseError("Anthropic прервал ответ с ошибкой.", detail)
        if status == 401:
            return ProviderConfigError("Ключ API не принят (401). Проверьте ключ в подключении.", detail)
        if status == 403:
            return ProviderConfigError(
                "Доступ запрещён (403): ключ не даёт доступа к этой модели, или Anthropic не обслуживает "
                "страну, из которой идёт запрос.",
                detail,
            )
        if status == 404:
            if listing:
                return ProviderConfigError(
                    "По этому адресу нет API Anthropic (404): проверьте адрес.", detail
                )
            return ProviderConfigError(f"Модель «{self.model}» не найдена у Anthropic (404).", detail)
        if status == 413:
            return ProviderConfigError("Запрос слишком большой для API Anthropic (413).", detail)
        if status in (400, 422):
            if detail and "credit balance" in detail.lower():
                return ProviderConfigError("Недостаточно средств на балансе Anthropic.", detail)
            return ProviderConfigError(f"Anthropic отклонил запрос ({status}).", detail)
        if status == 429:
            return ProviderUnavailableError(
                "Anthropic ограничил частоту запросов (429). Повторите позже.", detail
            )
        if status == 529:
            return ProviderUnavailableError("Anthropic перегружен (529). Повторите позже.", detail)
        if status >= 500 or status in (408, 409):
            return ProviderUnavailableError(
                f"Ошибка на стороне Anthropic ({status}). Повторите позже.", detail
            )
        return ProviderConfigError(f"Anthropic вернул ошибку {status}.", detail)
