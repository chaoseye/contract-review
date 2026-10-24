"""Профили подключений ИИ: хранение ключа, активный профиль, проверка настроек, команды CLI."""

import io
import json
from pathlib import Path

import httpx2
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.analysis import profiles
from app.analysis.providers.anthropic_api import AnthropicProvider
from app.analysis.providers.base import AnalysisResult, CheckReport, ModelInfo, ProviderConfigError, Usage
from app.analysis.providers.codex_cli import CodexCliProvider
from app.analysis.providers.openai_compat import OpenAICompatProvider
from app.core.config import Settings
from app.core.crypto import SecretKeyError, decrypt_secret, encrypt_secret, mask_secret
from app.core.db import make_engine, make_sessionmaker
from app.models import LlmKind, LlmProfile
from tests.conftest import make_settings, migrate

KEY = "sk-routerai-test-1234"


@pytest.fixture
def secured(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path, encryption_key=Fernet.generate_key().decode())
    migrate(settings)
    return settings


@pytest.fixture
def factory(secured: Settings):
    engine = make_engine(secured.database_url)
    yield make_sessionmaker(engine)
    engine.dispose()


def add_routerai(db: Session, settings: Settings, name: str = "RouterAI", **params: object) -> LlmProfile:
    return profiles.create_profile(
        db,
        settings,
        name=name,
        kind=LlmKind.OPENAI_COMPAT,
        base_url="https://routerai.ru/api/v1/",
        model="anthropic/claude-sonnet-5.5",
        api_key=KEY,
        params={"russia_only": True, **params},
    )


def test_encryption_round_trip(secured: Settings, tmp_path: Path) -> None:
    token = encrypt_secret(secured, KEY)
    assert KEY not in token
    assert decrypt_secret(secured, token) == KEY
    other = make_settings(tmp_path, encryption_key=Fernet.generate_key().decode())
    with pytest.raises(SecretKeyError, match="изменился"):
        decrypt_secret(other, token)
    with pytest.raises(SecretKeyError, match="Не задан"):
        encrypt_secret(make_settings(tmp_path), KEY)
    with pytest.raises(SecretKeyError, match="повреждён"):
        encrypt_secret(make_settings(tmp_path, encryption_key="not-a-fernet-key"), KEY)
    assert mask_secret(KEY) == "…1234"
    assert mask_secret("short") == "…"


def test_key_is_stored_encrypted(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        profile = add_routerai(db, secured)
        db.commit()
        assert profile.base_url == "https://routerai.ru/api/v1"
        assert profile.is_active  # первое подключение сразу становится активным
    with factory() as db:
        raw = db.execute(text("SELECT api_key_enc, params FROM llm_profiles")).one()
        assert KEY not in raw.api_key_enc
        assert KEY not in str(raw.params)
        profile = profiles.get_profile(db, "RouterAI")
        assert profiles.profile_params(profile).key_hint == "1234"
        provider = profiles.build_provider(profile, secured)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._client.api_key == KEY


def test_only_one_active_profile(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        first = add_routerai(db, secured, name="first")
        second = add_routerai(db, secured, name="second")
        assert first.is_active and not second.is_active
        profiles.activate_profile(db, second)
        db.commit()
    with factory() as db:
        assert profiles.active_profile(db).name == "second"
        assert [p.is_active for p in profiles.list_profiles(db)] == [False, True]
        # Защита на уровне базы: второй активный профиль не записать даже в обход кода.
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE llm_profiles SET is_active = 1 WHERE name = 'first'"))


def test_update_resets_model_info_and_key(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        profile = add_routerai(db, secured)
        snapshot = ModelInfo(id=profile.model, currency="RUB", price_input=0.0002).model_dump(mode="json")
        profiles.update_profile(db, secured, profile, params={"model_info": snapshot, "timeout_seconds": 600})
        assert profiles.profile_params(profile).model_info is not None
        profiles.update_profile(db, secured, profile, model="deepseek/deepseek-v4-flash")
        params = profiles.profile_params(profile)
        assert params.model_info is None
        assert params.timeout_seconds == 600
        profiles.update_profile(db, secured, profile, api_key="")
        assert profile.api_key_enc is None
        assert profiles.profile_params(profile).key_hint == ""


@pytest.mark.parametrize(
    ("kind", "base_url", "model", "params", "api_key", "message"),
    [
        ("openai_compat", "routerai.ru/api/v1", "m", {}, None, "http"),
        ("openai_compat", "https://routerai.ru/api/v1", "", {}, None, "модель"),
        ("openai_compat", "https://openrouter.ai/api/v1", "m", {"russia_only": True}, None, "RouterAI"),
        ("openai_compat", "https://routerai.ru/api/v1", "m", {"timeout_seconds": 1}, None, "timeout_seconds"),
        ("openai_compat", "https://routerai.ru/api/v1", "m", {"unknown": 1}, None, "unknown"),
        ("codex_cli", "https://api.openai.com/v1", "", {}, None, "адреса API"),
        ("codex_cli", "", "", {"russia_only": True}, None, "США"),
        ("codex_cli", "", "", {}, "sk-1", "ChatGPT"),
        ("codex_cli", "", "", {"effort": "high"}, None, "только у подключения Anthropic"),
        ("openai_compat", "https://api.openai.com/v1", "gpt-5.2", {"effort": "high"}, None, "effort"),
        ("anthropic", "", "", {}, None, "модель"),
        ("anthropic", "", "claude-opus-5-5", {"russia_only": True}, None, "за рубежом"),
        ("anthropic", "", "claude-opus-5-5", {"response_format": "json_object"}, None, "JSON-режима"),
        ("anthropic", "", "claude-opus-5-5", {"effort": "extreme"}, None, "effort"),
        ("ollama", "", "", {}, None, "Неизвестный"),
    ],
)
def test_invalid_settings(
    factory: sessionmaker[Session],
    secured: Settings,
    kind: str,
    base_url: str,
    model: str,
    params: dict,
    api_key: str | None,
    message: str,
) -> None:
    with factory() as db, pytest.raises(profiles.ProfileError, match=message):
        profiles.create_profile(
            db, secured, name="x", kind=kind, base_url=base_url, model=model, api_key=api_key, params=params
        )


def test_duplicate_name(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        add_routerai(db, secured)
        with pytest.raises(profiles.ProfileError, match="уже есть"):
            add_routerai(db, secured)


def test_codex_profile_builds_cli_provider(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        profile = profiles.create_profile(db, secured, name="Codex", kind=LlmKind.CODEX_CLI)
        provider = profiles.build_provider(profile, secured, codex_command=["codex"])
        assert isinstance(provider, CodexCliProvider)
        assert provider.home == secured.data_dir / "codex"
        assert provider.command == ["codex"]


def test_anthropic_profile(factory: sessionmaker[Session], secured: Settings) -> None:
    with factory() as db:
        profile = profiles.create_profile(
            db,
            secured,
            name="Claude",
            kind=LlmKind.ANTHROPIC,
            model="claude-opus-5-5",
            api_key="sk-ant-api03-test-5678",
            params={"effort": "high"},
        )
        db.commit()
        # Адрес по умолчанию — API Anthropic; ключ хранится так же, как у остальных подключений.
        assert profile.base_url == "https://api.anthropic.com"
        provider = profiles.build_provider(profile, secured)
        assert isinstance(provider, AnthropicProvider)
        assert provider._client.api_key == "sk-ant-api03-test-5678"
        assert provider.params.effort == "high"
        profiles.update_profile(db, secured, profile, base_url="https://api.anthropic.com/v1/")
        assert profile.base_url == "https://api.anthropic.com"
        # Без ключа подключение не собирается: понятная ошибка вместо запроса к API.
        profiles.update_profile(db, secured, profile, api_key="")
        with pytest.raises(ProviderConfigError, match="ключ"):
            profiles.build_provider(profile, secured)


def test_check_anthropic_profile_saves_model_snapshot(
    factory: sessionmaker[Session], secured: Settings
) -> None:
    from tests.test_anthropic_api import MODEL, stream, text

    replies = [httpx2.Response(200, json=MODEL), stream(text('{"ok": true, "answer": "готов"}'))]
    client = httpx2.Client(transport=httpx2.MockTransport(lambda request: replies.pop(0)))
    with factory() as db:
        profile = profiles.create_profile(
            db, secured, name="Claude", kind=LlmKind.ANTHROPIC, model="claude-opus-5-5", api_key="sk-ant-x"
        )
        report = profiles.check_profile(db, secured, profile, http_client=client)
        db.commit()
    assert report.result.response_format == "json_schema"
    with factory() as db:
        saved = profiles.profile_params(profiles.get_profile(db, "Claude")).model_info
        assert saved is not None
        assert saved.max_output_tokens == 128_000
        assert saved.supports("thinking_adaptive")


def test_check_profile_saves_model_snapshot(factory: sessionmaker[Session], secured: Settings) -> None:
    models = {
        "data": [{"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": 0.0002, "completion": 0.001}}]
    }
    endpoints = {
        "data": {
            "endpoints": [
                {"countries": ["ru"], "status": 0, "pricing": {"prompt": 0.0003, "completion": 0.002}}
            ]
        }
    }
    answer = {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "anthropic/claude-sonnet-5.5",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok": true, "answer": "готов"}'},
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48},
    }
    replies = [models, endpoints, answer]
    client = httpx2.Client(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=replies.pop(0)))
    )
    with factory() as db:
        profile = add_routerai(db, secured)
        report = profiles.check_profile(db, secured, profile, http_client=client)
        db.commit()
    assert report.result.usage.cost_rub == pytest.approx(40 * 0.0003 + 8 * 0.002)
    with factory() as db:
        saved = profiles.profile_params(profiles.get_profile(db, "RouterAI")).model_info
        assert saved is not None
        assert saved.countries == ["ru"]
        assert saved.price_input == pytest.approx(0.0003)


# --- команды CLI ------------------------------------------------------------------------------


@pytest.fixture
def run_cli(secured: Settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: secured)

    def run(*argv: str, stdin: str = "") -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        cli.main(list(argv))

    return run


def test_cli_manages_profiles(run_cli, capsys, factory: sessionmaker[Session]) -> None:
    run_cli(
        "llm-add",
        "RouterAI",
        "--preset",
        "routerai",
        "--model",
        "anthropic/claude-sonnet-5.5",
        stdin=f"{KEY}\n",
    )
    run_cli(
        "llm-add",
        "Codex",
        "--kind",
        "codex_cli",
        "--timeout",
        "900",
        "--codex-config",
        'model_reasoning_effort="high"',
    )
    run_cli("llm-add", "Local", "--preset", "ollama", "--model", "qwen3")
    capsys.readouterr()

    run_cli("llm-list")
    listing = capsys.readouterr().out
    assert KEY not in listing
    lines = {
        line.split()[1] if line.startswith("*") else line.split()[0]: line for line in listing.splitlines()
    }
    assert lines["RouterAI"].startswith("*")
    assert "ключ …1234" in lines["RouterAI"]
    assert not lines["RouterAI"].endswith("РФ")
    assert "codex_cli" in lines["Codex"] and "900 с" in lines["Codex"]
    assert "без ключа" in lines["Local"]

    run_cli("llm-set", "RouterAI", "--model", "deepseek/deepseek-v4-flash", "--max-output-tokens", "32000")
    run_cli("llm-set", "RouterAI", "--key", stdin="sk-new-key-9876\n")
    run_cli("llm-activate", "Codex")
    run_cli("llm-delete", "Local")
    with factory() as db:
        names = {p.name: p for p in profiles.list_profiles(db)}
        assert set(names) == {"Codex", "RouterAI"}
        assert names["Codex"].is_active
        router = names["RouterAI"]
        assert router.model == "deepseek/deepseek-v4-flash"
        params = profiles.profile_params(router)
        assert params.max_output_tokens == 32000
        assert params.key_hint == "9876"
        # Пресет RouterAI «Только серверы в РФ» не включает: зарубежные модели разрешены.
        assert params.russia_only is False
        assert profiles.profile_params(names["Codex"]).codex_config == ['model_reasoning_effort="high"']
    # Галочка включается отдельно и видна в списке.
    run_cli("llm-set", "RouterAI", "--russia-only")
    capsys.readouterr()
    run_cli("llm-list")
    assert next(line for line in capsys.readouterr().out.splitlines() if "RouterAI" in line).endswith("РФ")


def test_cli_anthropic_profile(run_cli, capsys, factory: sessionmaker[Session], monkeypatch) -> None:
    run_cli(
        "llm-add", "Claude", "--preset", "anthropic", "--model", "claude-opus-5-5", stdin="sk-ant-cli-4321\n"
    )
    with factory() as db:
        profile = profiles.get_profile(db, "Claude")
        assert profile.kind == LlmKind.ANTHROPIC
        assert profile.base_url == "https://api.anthropic.com"
        # У готового адреса Anthropic глубина рассуждений — high: анализ договора — сложная задача.
        assert profiles.profile_params(profile).effort == "high"
    run_cli("llm-set", "Claude", "--effort", "max")
    capsys.readouterr()
    run_cli("llm-list")
    line = next(line for line in capsys.readouterr().out.splitlines() if "Claude" in line)
    assert "anthropic" in line and "effort max" in line and "ключ …4321" in line

    models = [ModelInfo(id="claude-opus-5-5", name="Claude Opus 5.5", context_length=1_000_000)]
    monkeypatch.setattr(AnthropicProvider, "list_models", lambda self: models)
    run_cli("llm-models", "Claude")
    out = capsys.readouterr().out
    assert "claude-opus-5-5 | 1000K" in out
    assert "не сообщает цен" in out

    with pytest.raises(SystemExit, match="для подключений anthropic"):
        run_cli(
            "llm-add", "X", "--preset", "anthropic", "--kind", "openai_compat", "--model", "m", stdin="k\n"
        )
    with pytest.raises(SystemExit, match="Ключ не введён"):
        run_cli("llm-add", "Y", "--preset", "anthropic", "--model", "claude-opus-5-5", stdin="\n")


def test_cli_reports_errors_without_traceback(run_cli) -> None:
    with pytest.raises(SystemExit, match="не найдено"):
        run_cli("llm-activate", "nope")
    with pytest.raises(SystemExit, match="Ключ не введён"):
        run_cli("llm-add", "RouterAI", "--preset", "routerai", "--model", "m", stdin="\n")
    with pytest.raises(SystemExit, match="только с RouterAI"):
        run_cli("llm-add", "OR", "--preset", "openrouter", "--model", "m", "--russia-only", stdin="sk-x\n")
    with pytest.raises(SystemExit, match="не JSON"):
        run_cli("llm-add", "X", "--preset", "ollama", "--model", "m", "--extra-body", "{bad")


def test_cli_test_prints_report(run_cli, capsys, monkeypatch) -> None:
    run_cli("llm-add", "RouterAI", "--preset", "routerai", "--model", "vendor/model", stdin=f"{KEY}\n")
    report = CheckReport(
        model_info=ModelInfo(
            id="vendor/model",
            name="Vendor Model",
            context_length=262144,
            currency="RUB",
            price_input=0.000217776,
            price_output=0.00108888,
            countries=["ru"],
        ),
        result=AnalysisResult(
            data={"ok": True},
            usage=Usage(input_tokens=40, output_tokens=8, cost_rub=0.0174, cost_estimated=True),
            model="vendor/model",
            response_format="json_schema",
            duration_seconds=1.25,
        ),
        notes=["замечание"],
    )
    monkeypatch.setattr(profiles, "check_profile", lambda db, settings, profile: report)
    capsys.readouterr()
    run_cli("llm-test", "RouterAI")
    out = capsys.readouterr().out
    assert "связь есть" in out
    assert "вход 217,78 ₽, выход 1088,88 ₽ (провайдеры с серверами в РФ)" in out
    assert "Ответ за 1,2 с" in out or "Ответ за 1,3 с" in out
    assert "стоимость ≈ 0,0174 ₽" in out
    assert "! замечание" in out
    assert json.dumps(report.result.data)  # отчёт не падает на данных


def test_cli_does_not_import_ai_sdks_for_other_commands() -> None:
    import subprocess
    import sys

    code = "import sys, app.cli; print('openai' in sys.modules or 'anthropic' in sys.modules)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[1],
    )
    assert result.stdout.strip() == "False"
