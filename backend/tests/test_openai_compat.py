"""Подключение openai_compat на подменённом HTTP-транспорте: запрос, разбор ответа, ошибки, цены, модели."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.analysis.providers.base import (
    PING_REQUEST,
    AnalysisRequest,
    ModelInfo,
    ProfileParams,
    ProviderConfigError,
    ProviderResponseError,
    ProviderUnavailableError,
    parse_json_object,
)
from app.analysis.providers.openai_compat import OpenAICompatProvider, price_currency
from app.core.logging import configure_logging

ROUTERAI = "https://routerai.ru/api/v1"
SCHEMA = {
    "type": "object",
    "properties": {"risks": {"type": "array", "items": {"type": "string"}}},
    "required": ["risks"],
    "additionalProperties": False,
}
REQUEST = AnalysisRequest(system="Промпт администратора", user="[B0001] (п. 1.1) Текст", schema=SCHEMA)


def completion(content: str, finish: str = "stop", usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "c-1",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/model",
        "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}
        ],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    }


class Server:
    """Подменённый провайдер: отвечает по очереди из responses и запоминает запросы."""

    def __init__(self, *responses: httpx2.Response | Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        return response(request) if callable(response) else response

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))


def ok(payload: Any, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=payload)


def make(
    server: Server, base_url: str = ROUTERAI, model: str = "vendor/model", **params: Any
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url=base_url,
        api_key="sk-test-key-0000",
        model=model,
        params=ProfileParams.model_validate({"max_retries": 0, **params}),
        http_client=server.client(),
    )


def info(**fields: Any) -> dict[str, Any]:
    return ModelInfo(id="vendor/model", **fields).model_dump(mode="json")


def test_structured_outputs_request() -> None:
    server = Server(ok(completion('{"risks": ["п. 9.2"]}')))
    result = make(server, russia_only=True, extra_body={"reasoning": {"effort": "high"}}).analyze(REQUEST)

    assert result.data == {"risks": ["п. 9.2"]}
    assert result.response_format == "json_schema"
    request = server.requests[0]
    assert str(request.url) == f"{ROUTERAI}/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-key-0000"
    body = server.body()
    assert body["model"] == "vendor/model"
    assert body["messages"] == [
        {"role": "system", "content": "Промпт администратора"},
        {"role": "user", "content": "[B0001] (п. 1.1) Текст"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "analysis", "strict": True, "schema": SCHEMA},
    }
    assert body["provider"] == {"country": "ru"}
    assert body["reasoning"] == {"effort": "high"}
    # Необязательные параметры не отправляются, пока их не задали.
    assert "temperature" not in body
    assert "max_tokens" not in body


def test_optional_parameters() -> None:
    server = Server(ok(completion('{"risks": []}')), ok(completion('{"risks": []}')))
    make(server, temperature=0.2, max_output_tokens=16000).analyze(REQUEST)
    assert server.body()["temperature"] == 0.2
    assert server.body()["max_tokens"] == 16000
    only_new = info(supported_parameters=["structured_outputs", "max_completion_tokens"])
    make(server, max_output_tokens=16000, model_info=only_new).analyze(REQUEST)
    assert server.body()["max_completion_tokens"] == 16000
    assert "max_tokens" not in server.body()


def test_openai_gets_max_completion_tokens() -> None:
    """Модели OpenAI с рассуждениями (o-серия, GPT-5) не принимают max_tokens."""
    server = Server(ok(completion('{"risks": []}')))
    make(server, base_url="https://api.openai.com/v1", model="gpt-5.2", max_output_tokens=16000).analyze(
        REQUEST
    )
    assert server.body()["max_completion_tokens"] == 16000
    assert "max_tokens" not in server.body()


def test_openai_models_list_keeps_chat_models() -> None:
    names = [
        "gpt-5.2",
        "gpt-5-mini",
        "o3",
        "o4-mini",
        "gpt-4.1",
        "text-embedding-3-large",
        "omni-moderation-latest",
        "whisper-1",
        "gpt-4o-mini-tts",
        "gpt-4o-transcribe",
        "gpt-realtime",
        "gpt-audio",
        "dall-e-3",
        "gpt-image-1",
        "sora-2",
        "gpt-4o-search-preview",
        "computer-use-preview",
        "davinci-002",
        "gpt-3.5-turbo-instruct",
        "gpt-5-codex",
        "o3-pro",
        "gpt-5-pro-2025-10-06",
    ]
    listing = {
        "object": "list",
        "data": [{"id": name, "object": "model", "owned_by": "openai"} for name in names],
    }
    models = make(Server(ok(listing)), base_url="https://api.openai.com/v1").list_models()
    assert [model.id for model in models] == ["gpt-4.1", "gpt-5-mini", "gpt-5.2", "o3", "o4-mini"]
    # У других адресов список не фильтруется по названиям.
    assert len(make(Server(ok(listing)), base_url="http://localhost:8000/v1").list_models()) == len(names)


def test_falls_back_to_json_mode_when_schema_is_rejected() -> None:
    rejected = ok({"error": {"message": "response_format json_schema is not supported"}}, status=400)
    server = Server(rejected, ok(completion('{"risks": []}')))
    result = make(server).analyze(REQUEST)

    assert result.response_format == "json_object"
    assert server.body(0)["response_format"]["type"] == "json_schema"
    second = server.body(1)
    assert second["response_format"] == {"type": "json_object"}
    # Без structured outputs схема уходит в инструкцию.
    assert '"additionalProperties": false' in second["messages"][0]["content"]


def test_fixed_mode_does_not_fall_back() -> None:
    server = Server(ok({"error": {"message": "bad schema"}}, status=400))
    with pytest.raises(ProviderConfigError, match="отклонил") as error:
        make(server, response_format="json_schema").analyze(REQUEST)
    assert error.value.detail == "bad schema"
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("supported", "expected"),
    [(["response_format"], {"type": "json_object"}), (["temperature"], None)],
)
def test_auto_mode_follows_model_capabilities(supported: list[str], expected: dict | None) -> None:
    server = Server(ok(completion('{"risks": []}')))
    make(server, model_info=info(supported_parameters=supported)).analyze(REQUEST)
    assert server.body().get("response_format") == expected
    assert "JSON Schema" in server.body()["messages"][0]["content"]


def test_answer_in_markdown_fence_is_parsed() -> None:
    server = Server(ok(completion('Вот ответ:\n```json\n{"risks": ["a"]}\n```')))
    assert make(server).analyze(REQUEST).data == {"risks": ["a"]}


def test_answer_that_is_not_json() -> None:
    server = Server(ok(completion("Пункт 9.2 рискованный")))
    with pytest.raises(ProviderResponseError) as error:
        make(server).analyze(REQUEST)
    # Текст ответа (в нём могут быть цитаты договора) в сообщение не попадает.
    assert "9.2" not in error.value.message
    assert error.value.detail is None


def test_truncated_answer() -> None:
    server = Server(ok(completion('{"risks": [', finish="length")))
    with pytest.raises(ProviderResponseError, match="обрезан"):
        make(server).analyze(REQUEST)


@pytest.mark.parametrize(
    ("status", "error_type", "text", "retryable"),
    [
        (401, ProviderConfigError, "Ключ API не принят", False),
        (402, ProviderConfigError, "Недостаточно средств", False),
        (403, ProviderConfigError, "Доступ запрещён", False),
        (404, ProviderConfigError, "не найдена", False),
        (429, ProviderUnavailableError, "частоту запросов", True),
        (502, ProviderUnavailableError, "на стороне провайдера", True),
    ],
)
def test_http_errors(status: int, error_type: type, text: str, retryable: bool) -> None:
    server = Server(ok({"error": {"message": f"ошибка {status}"}}, status=status))
    with pytest.raises(error_type, match=text) as error:
        make(server, response_format="json_schema").analyze(REQUEST)
    assert error.value.retryable is retryable
    assert error.value.detail == f"ошибка {status}"


def test_not_found_mentions_russian_servers() -> None:
    server = Server(ok({"error": {"message": "No endpoints"}}, status=404))
    with pytest.raises(ProviderConfigError, match="серверами в РФ"):
        make(server, russia_only=True, response_format="json_schema").analyze(REQUEST)


def test_network_errors() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timeout", request=request)

    with pytest.raises(ProviderUnavailableError, match="Нет связи"):
        make(Server(refuse)).analyze(REQUEST)
    with pytest.raises(ProviderUnavailableError, match="не ответила"):
        make(Server(hang)).analyze(REQUEST)


def test_sdk_retries_transient_errors() -> None:
    server = Server(ok({"error": {"message": "busy"}}, status=503), ok(completion('{"risks": []}')))
    provider = OpenAICompatProvider(
        base_url=ROUTERAI,
        api_key="k",
        model="vendor/model",
        params=ProfileParams(max_retries=1),
        http_client=server.client(),
    )
    assert provider.analyze(REQUEST).data == {"risks": []}
    assert len(server.requests) == 2


def test_cost_reported_by_routerai() -> None:
    usage = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost": 0.4532}
    result = make(Server(ok(completion('{"risks": []}', usage=usage)))).analyze(REQUEST)
    assert result.usage.cost_rub == pytest.approx(0.4532)
    assert not result.usage.cost_estimated


def test_cost_estimated_from_prices() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 400},
    }
    prices = info(currency="RUB", price_input=0.0002, price_output=0.001, price_cache_read=0.00002)
    result = make(Server(ok(completion('{"risks": []}', usage=usage))), model_info=prices).analyze(REQUEST)
    assert result.usage.cached_input_tokens == 400
    assert result.usage.cost_rub == pytest.approx(600 * 0.0002 + 400 * 0.00002 + 200 * 0.001)
    assert result.usage.cost_estimated


def test_cost_is_not_counted_in_rubles_for_other_providers() -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.001}
    result = make(
        Server(ok(completion('{"risks": []}', usage=usage))), base_url="https://openrouter.ai/api/v1"
    ).analyze(REQUEST)
    assert result.usage.cost_rub is None
    assert price_currency("https://api.openai.com/v1") is None


ROUTERAI_MODELS = {
    "data": [
        {
            "id": "vendor/model",
            "name": "Vendor: Model",
            "context_length": 262144,
            "architecture": {"output_modalities": ["text"]},
            "pricing": {"prompt": 2.27e-05, "completion": "3.37e-05", "input_cache_read": "-1"},
            "supported_parameters": ["response_format", "structured_outputs"],
        },
        {"id": "vendor/painter", "architecture": {"output_modalities": ["image"]}, "pricing": {"image": 1}},
        {"id": "alpha/model", "name": "Alpha"},
    ]
}
ENDPOINTS = {
    "data": {
        "id": "vendor/model",
        "endpoints": [
            {
                "tag": "cheap-us",
                "countries": ["us"],
                "status": 0,
                "pricing": {"prompt": 1e-05, "completion": 2e-05},
            },
            {
                "tag": "down-ru",
                "countries": ["ru"],
                "status": -2,
                "pricing": {"prompt": 1e-06, "completion": 1e-06},
            },
            {
                "tag": "yandex",
                "countries": ["ru"],
                "status": 0,
                "pricing": {"prompt": 0.00039, "completion": 0.00065},
                "supported_parameters": ["structured_outputs", "max_tokens"],
            },
        ],
    }
}


def test_list_models_with_prices_in_rubles() -> None:
    server = Server(ok(ROUTERAI_MODELS))
    models = make(server).list_models()
    assert [model.id for model in models] == ["alpha/model", "vendor/model"]
    model = models[1]
    assert model.currency == "RUB"
    assert model.price_input == pytest.approx(2.27e-05)
    assert model.price_output == pytest.approx(3.37e-05)
    assert model.price_cache_read is None
    assert model.context_length == 262144
    assert model.supports("structured_outputs")
    assert models[0].currency is None
    assert models[0].supports("structured_outputs") is None
    assert str(server.requests[0].url) == f"{ROUTERAI}/models"


def test_russian_servers_price_comes_from_russian_provider() -> None:
    server = Server(ok(ROUTERAI_MODELS), ok(ENDPOINTS))
    model = make(server, russia_only=True).describe_model()
    assert str(server.requests[1].url) == f"{ROUTERAI}/models/vendor/model/endpoints"
    assert model.countries == ["ru"]
    assert model.price_input == pytest.approx(0.00039)
    assert model.price_output == pytest.approx(0.00065)
    assert model.supported_parameters == ["structured_outputs", "max_tokens"]
    assert model.checked_at is not None


def test_model_without_russian_servers() -> None:
    endpoints = {"data": {"endpoints": [{"tag": "us", "countries": ["us"], "status": 0, "pricing": {}}]}}
    with pytest.raises(ProviderConfigError, match="нет провайдеров с серверами в РФ"):
        make(Server(ok(ROUTERAI_MODELS), ok(endpoints)), russia_only=True).describe_model()


def test_unknown_model() -> None:
    with pytest.raises(ProviderConfigError, match="нет в списке"):
        make(Server(ok(ROUTERAI_MODELS)), model="vendor/typo").describe_model()


def test_check_connection() -> None:
    server = Server(ok(ROUTERAI_MODELS), ok(completion('{"ok": true, "answer": "готов"}')))
    report = make(server).check()
    assert report.model_info is not None and report.model_info.id == "vendor/model"
    assert report.result.data == {"ok": True, "answer": "готов"}
    assert report.notes == []
    ping = server.body(1)
    assert ping["response_format"]["json_schema"]["name"] == PING_REQUEST.schema_name
    assert "договор" not in json.dumps(ping, ensure_ascii=False).lower()


def test_check_local_server_without_models_list() -> None:
    server = Server(ok({"error": "not found"}, status=404), ok(completion('{"ok": true, "answer": "готов"}')))
    report = make(server, base_url="http://localhost:11434/v1", model="qwen3").check()
    assert report.model_info is None
    assert report.notes and "Список моделей не получен" in report.notes[0]
    assert report.result.data["ok"] is True


def test_check_warns_when_answer_does_not_follow_schema() -> None:
    server = Server(ok(ROUTERAI_MODELS), ok(completion('{"ok": false, "answer": "нет"}')))
    assert make(server).check().notes


def test_russia_only_needs_routerai() -> None:
    with pytest.raises(ProviderConfigError, match="только с RouterAI"):
        make(Server(), base_url="https://openrouter.ai/api/v1", russia_only=True)


def test_parse_json_object_variants() -> None:
    assert parse_json_object('  {"a": 1}  ') == {"a": 1}
    assert parse_json_object('Ответ: {"a": {"b": 2}} — готово') == {"a": {"b": 2}}
    with pytest.raises(ProviderResponseError):
        parse_json_object("[1, 2]")
    with pytest.raises(ProviderResponseError):
        parse_json_object("")


def test_http_loggers_stay_quiet_in_debug_mode() -> None:
    configure_logging("DEBUG")
    try:
        for name in ("openai", "anthropic", "httpx2", "httpcore2"):
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG
    finally:
        configure_logging("WARNING")
