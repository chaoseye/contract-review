"""Подключение anthropic на подменённом HTTP-транспорте: запрос, поток, разбор ответа, ошибки, модели."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.analysis.providers import anthropic_api
from app.analysis.providers.anthropic_api import AnthropicProvider, model_info_from_api, normalize_base_url
from app.analysis.providers.base import (
    PING_REQUEST,
    AnalysisRequest,
    ModelInfo,
    ProfileParams,
    ProviderConfigError,
    ProviderResponseError,
    ProviderUnavailableError,
)

API = "https://api.anthropic.com"
SCHEMA = {
    "type": "object",
    "properties": {"risks": {"type": "array", "items": {"type": "string"}}},
    "required": ["risks"],
    "additionalProperties": False,
}
REQUEST = AnalysisRequest(system="Промпт администратора", user="[B0001] (п. 1.1) Текст", schema=SCHEMA)
FULL_CAPABILITIES = [
    "structured_outputs",
    "effort",
    "effort_low",
    "effort_medium",
    "effort_high",
    "effort_xhigh",
    "effort_max",
    "thinking_adaptive",
]


def stream(
    *blocks: dict[str, Any],
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
    model: str = "claude-opus-5-5",
    usage: dict[str, Any] | None = None,
) -> httpx2.Response:
    """Ответ Messages API потоком (SSE): блоки text, thinking или fallback, затем причина остановки."""
    events = [message_start(model, usage)]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events.append(("content_block_start", _start(index, {"type": "text", "text": ""})))
            events.append(
                ("content_block_delta", _delta(index, {"type": "text_delta", "text": block["text"]}))
            )
        elif block["type"] == "thinking":
            events.append(
                ("content_block_start", _start(index, {"type": "thinking", "thinking": "", "signature": ""}))
            )
            events.append(
                ("content_block_delta", _delta(index, {"type": "signature_delta", "signature": "s"}))
            )
        else:
            events.append(("content_block_start", _start(index, block)))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    events.append(
        ("message_delta", {"type": "message_delta", "delta": delta, "usage": {"output_tokens": 300}})
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return sse(events)


def message_start(
    model: str = "claude-opus-5-5", usage: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 1, **(usage or {})},
    }
    return "message_start", {"type": "message_start", "message": message}


def _start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, delta: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def sse(events: list[tuple[str, dict[str, Any]]]) -> httpx2.Response:
    body = "".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events
    )
    return httpx2.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def error(status: int, kind: str, message: str) -> httpx2.Response:
    return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": message}})


class Server:
    """Подменённый API: отвечает по очереди из responses и запоминает запросы."""

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


def make(
    server: Server, base_url: str = API, model: str = "claude-opus-5-5", **params: Any
) -> AnthropicProvider:
    return AnthropicProvider(
        base_url=base_url,
        api_key="sk-ant-test-0000",
        model=model,
        params=ProfileParams.model_validate({"max_retries": 0, **params}),
        http_client=server.client(),
    )


def info(model: str = "claude-opus-5-5", **fields: Any) -> dict[str, Any]:
    return ModelInfo(id=model, **fields).model_dump(mode="json")


def test_structured_outputs_request() -> None:
    server = Server(stream(text('{"risks": ["п. 9.2"]}')))
    result = make(server, effort="high", model_info=info(supported_parameters=FULL_CAPABILITIES)).analyze(
        REQUEST
    )

    assert result.data == {"risks": ["п. 9.2"]}
    assert result.response_format == "json_schema"
    assert result.model == "claude-opus-5-5"
    request = server.requests[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test-0000"
    assert request.headers["anthropic-version"] == "2023-06-01"
    # Отказ классификатора у Claude Opus 5.5 повторяется на резервной модели самим API.
    assert request.headers["anthropic-beta"] == anthropic_api.FALLBACK_BETA
    body = server.body()
    assert body["stream"] is True
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == anthropic_api.DEFAULT_MAX_TOKENS
    assert body["system"] == [
        {"type": "text", "text": "Промпт администратора", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"] == [{"role": "user", "content": "[B0001] (п. 1.1) Текст"}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "high"}
    assert body["thinking"] == {"type": "adaptive"}
    assert body["fallbacks"] == "default"
    # Необязательные параметры не отправляются, пока их не задали.
    assert "temperature" not in body


def test_without_model_snapshot_only_set_parameters_are_sent() -> None:
    server = Server(stream(text('{"risks": []}')))
    make(server, model="claude-haiku-4-5", base_url="http://127.0.0.1:8766/v1").analyze(REQUEST)
    request, body = server.requests[0], server.body()
    assert str(request.url) == "http://127.0.0.1:8766/v1/messages"
    # Резервная модель есть только у части моделей и только у самого API Anthropic.
    assert "anthropic-beta" not in request.headers
    assert "fallbacks" not in body
    assert "thinking" not in body
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_optional_parameters() -> None:
    server = Server(stream(text('{"risks": []}')), stream(text('{"risks": []}')))
    make(server, temperature=0.2, max_output_tokens=16000, extra_body={"inference_geo": "us"}).analyze(
        REQUEST
    )
    body = server.body()
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 16000
    assert body["inference_geo"] == "us"
    # Предел ответа берётся у модели, если он меньше значения по умолчанию.
    make(server, model_info=info(max_output_tokens=8192)).analyze(REQUEST)
    assert server.body()["max_tokens"] == 8192


def test_effort_the_model_does_not_support_is_not_sent() -> None:
    server = Server(stream(text('{"risks": []}')))
    snapshot = info(supported_parameters=["structured_outputs", "effort", "effort_high", "thinking_adaptive"])
    make(server, effort="xhigh", model_info=snapshot).analyze(REQUEST)
    assert "effort" not in server.body()["output_config"]


def test_answer_after_thinking_and_fallback() -> None:
    fallback = {
        "type": "fallback",
        "from": {"model": "claude-opus-5-5"},
        "to": {"model": "claude-opus-4-8"},
        "trigger": {"type": "refusal", "category": "cyber"},
    }
    server = Server(
        stream({"type": "thinking"}, text('{"risks": ["обрыв'), fallback, text('{"risks": ["п. 2"]}'))
    )
    result = make(server).analyze(REQUEST)
    assert result.data == {"risks": ["п. 2"]}


def test_instruction_mode_has_schema_in_system() -> None:
    server = Server(stream(text('Вот ответ: {"risks": []}')))
    result = make(server, model_info=info(supported_parameters=["effort"])).analyze(REQUEST)
    assert result.response_format == "none"
    body = server.body()
    assert "output_config" not in body
    assert '"additionalProperties": false' in body["system"][0]["text"]


def test_falls_back_to_instruction_when_schema_is_rejected() -> None:
    rejected = error(400, "invalid_request_error", "output_config.format: schema is too complex")
    server = Server(rejected, stream(text('{"risks": []}')))
    result = make(server).analyze(REQUEST)
    assert result.response_format == "none"
    assert server.body(0)["output_config"]["format"]["type"] == "json_schema"
    assert "output_config" not in server.body(1)


def test_fixed_mode_does_not_fall_back() -> None:
    server = Server(error(400, "invalid_request_error", "bad schema"))
    with pytest.raises(ProviderConfigError, match="отклонил") as raised:
        make(server, response_format="json_schema").analyze(REQUEST)
    assert raised.value.detail == "bad schema"
    assert len(server.requests) == 1


def test_json_object_mode_is_not_available() -> None:
    with pytest.raises(ProviderConfigError, match="JSON-режима"):
        make(Server(), response_format="json_object").analyze(REQUEST)


def test_refusal() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "п. 9.2 договора"}
    server = Server(stream(text(""), stop_reason="refusal", stop_details=details))
    with pytest.raises(ProviderResponseError, match="отказалась") as raised:
        make(server).analyze(REQUEST)
    assert "cyber" in raised.value.message
    # Пояснение модели (в нём может быть текст договора) в сообщение не попадает.
    assert "9.2" not in raised.value.message
    assert raised.value.feedback is None


def test_truncated_answer() -> None:
    server = Server(stream(text('{"risks": ['), stop_reason="max_tokens"))
    with pytest.raises(ProviderResponseError, match="обрезан"):
        make(server).analyze(REQUEST)


def test_answer_that_is_not_json() -> None:
    server = Server(stream(text("Пункт 9.2 рискованный")))
    with pytest.raises(ProviderResponseError) as raised:
        make(server, response_format="none").analyze(REQUEST)
    assert "9.2" not in raised.value.message
    assert raised.value.feedback is not None


def test_usage_counts_cached_input() -> None:
    usage = {"input_tokens": 200, "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 300}
    result = make(Server(stream(text('{"risks": []}'), usage=usage))).analyze(REQUEST)
    assert result.usage.input_tokens == 5500
    assert result.usage.cached_input_tokens == 5000
    assert result.usage.output_tokens == 300
    # Цен в API нет: стоимость в рублях не считается.
    assert result.usage.cost_rub is None


def test_answer_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(anthropic_api, "_now", lambda: next(clock))
    with pytest.raises(ProviderUnavailableError, match="не ответила за 300 с"):
        make(Server(stream(text('{"risks": []}')))).analyze(REQUEST)


@pytest.mark.parametrize(
    ("status", "kind", "error_type", "message", "retryable"),
    [
        (401, "authentication_error", ProviderConfigError, "Ключ API не принят", False),
        (403, "permission_error", ProviderConfigError, "не обслуживает страну", False),
        (404, "not_found_error", ProviderConfigError, "не найдена", False),
        (413, "request_too_large", ProviderConfigError, "слишком большой", False),
        (429, "rate_limit_error", ProviderUnavailableError, "частоту запросов", True),
        (500, "api_error", ProviderUnavailableError, "на стороне Anthropic", True),
        (529, "overloaded_error", ProviderUnavailableError, "перегружен", True),
    ],
)
def test_http_errors(status: int, kind: str, error_type: type, message: str, retryable: bool) -> None:
    server = Server(error(status, kind, f"ошибка {status}"))
    with pytest.raises(error_type, match=message) as raised:
        make(server, response_format="json_schema").analyze(REQUEST)
    assert raised.value.retryable is retryable
    assert raised.value.detail == f"ошибка {status}"


def test_low_balance() -> None:
    server = Server(error(400, "invalid_request_error", "Your credit balance is too low to access the API"))
    with pytest.raises(ProviderConfigError, match="Недостаточно средств"):
        make(server, response_format="json_schema").analyze(REQUEST)


def test_error_inside_stream() -> None:
    def broken(kind: str) -> httpx2.Response:
        return sse(
            [message_start(), ("error", {"type": "error", "error": {"type": kind, "message": "сбой"}})]
        )

    with pytest.raises(ProviderUnavailableError, match="перегрузки"):
        make(Server(broken("overloaded_error"))).analyze(REQUEST)
    with pytest.raises(ProviderResponseError, match="прервал"):
        make(Server(broken("invalid_request_error"))).analyze(REQUEST)


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
    server = Server(error(529, "overloaded_error", "busy"), stream(text('{"risks": []}')))
    provider = AnthropicProvider(
        base_url=API,
        api_key="k",
        model="claude-opus-5-5",
        params=ProfileParams(max_retries=1),
        http_client=server.client(),
    )
    assert provider.analyze(REQUEST).data == {"risks": []}
    assert len(server.requests) == 2


def test_settings_errors() -> None:
    with pytest.raises(ProviderConfigError, match="ключ"):
        AnthropicProvider(base_url=API, api_key="", model="claude-opus-5-5", params=ProfileParams())
    with pytest.raises(ProviderConfigError, match="модель"):
        AnthropicProvider(base_url=API, api_key="k", model="", params=ProfileParams())
    with pytest.raises(ProviderConfigError, match="RouterAI"):
        AnthropicProvider(base_url=API, api_key="k", model="m", params=ProfileParams(russia_only=True))


def test_base_url_is_normalized() -> None:
    assert normalize_base_url("https://api.anthropic.com/v1/") == API
    assert normalize_base_url("") == API
    assert normalize_base_url("http://gateway.local/anthropic") == "http://gateway.local/anthropic"


MODEL = {
    "type": "model",
    "id": "claude-opus-5-5",
    "display_name": "Claude Opus 5.5",
    "created_at": "2026-09-01T00:00:00Z",
    "max_input_tokens": 1_000_000,
    "max_tokens": 128_000,
    "capabilities": {
        "batch": {"supported": True},
        "citations": {"supported": True},
        "code_execution": {"supported": True},
        "context_management": {
            "supported": True,
            "clear_thinking_20251015": {"supported": True},
            "clear_tool_uses_20250919": {"supported": True},
            "compact_20260112": {"supported": True},
        },
        "effort": {
            "supported": True,
            "low": {"supported": True},
            "medium": {"supported": True},
            "high": {"supported": True},
            "xhigh": {"supported": True},
            "max": {"supported": True},
        },
        "image_input": {"supported": True},
        "pdf_input": {"supported": True},
        "structured_outputs": {"supported": True},
        "thinking": {
            "supported": True,
            "types": {"adaptive": {"supported": True}, "enabled": {"supported": False}},
        },
    },
}
OLD_MODEL = {
    "type": "model",
    "id": "claude-3-haiku",
    "display_name": "Claude Haiku 3",
    "created_at": "2024-03-07T00:00:00Z",
    "max_input_tokens": 200_000,
    "max_tokens": 4096,
    "capabilities": None,
}


def page(*models: dict[str, Any]) -> httpx2.Response:
    ids = [model["id"] for model in models]
    return httpx2.Response(
        200,
        json={"data": list(models), "has_more": False, "first_id": ids[0], "last_id": ids[-1]},
    )


def test_list_models() -> None:
    server = Server(page(MODEL, OLD_MODEL))
    models = make(server).list_models()
    assert server.requests[0].url.path == "/v1/models"
    assert [model.id for model in models] == ["claude-opus-5-5", "claude-3-haiku"]
    opus, old = models
    assert opus.name == "Claude Opus 5.5"
    assert opus.context_length == 1_000_000
    assert opus.max_output_tokens == 128_000
    assert opus.supported_parameters == FULL_CAPABILITIES
    assert opus.currency is None
    assert old.supported_parameters is None
    assert old.supports("structured_outputs") is None


def test_model_info_without_optional_fields() -> None:
    model = type("Model", (), {"id": "claude-x", "display_name": None, "capabilities": None})()
    assert model_info_from_api(model) == ModelInfo(id="claude-x")


def test_check_connection() -> None:
    server = Server(httpx2.Response(200, json=MODEL), stream(text('{"ok": true, "answer": "готов"}')))
    report = make(server, effort="high").check()
    assert server.requests[0].url.path == "/v1/models/claude-opus-5-5"
    assert report.model_info is not None and report.model_info.max_output_tokens == 128_000
    assert report.model_info.checked_at is not None
    assert report.result.data == {"ok": True, "answer": "готов"}
    assert report.notes == []
    ping = server.body(1)
    assert ping["output_config"]["format"]["schema"] == PING_REQUEST.schema
    assert ping["thinking"] == {"type": "adaptive"}
    assert "договор" not in json.dumps(ping, ensure_ascii=False).lower()


def test_check_old_model_without_schema() -> None:
    old = {**OLD_MODEL, "capabilities": {**MODEL["capabilities"], "structured_outputs": {"supported": False}}}
    old["capabilities"]["effort"] = {"supported": False}
    server = Server(httpx2.Response(200, json=old), stream(text('{"ok": true, "answer": "готов"}')))
    report = make(server, model="claude-3-haiku", effort="high").check()
    assert report.result.response_format == "none"
    assert any("строгую схему" in note for note in report.notes)
    assert any("глубину рассуждений" in note for note in report.notes)
    assert server.body(1)["max_tokens"] == 4096


def test_check_unknown_model() -> None:
    server = Server(error(404, "not_found_error", "model: claude-typo"))
    with pytest.raises(ProviderConfigError, match="нет у Anthropic"):
        make(server, model="claude-typo").check()
