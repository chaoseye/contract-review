"""Подключение Codex CLI на поддельном codex (tests/fake_codex.py): аргументы, окружение, ошибки, вход."""

import json
import sys
from pathlib import Path

import pytest

from app.analysis.providers.base import (
    PING_REQUEST,
    AnalysisRequest,
    ProfileParams,
    ProviderConfigError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.analysis.providers.codex_cli import (
    PREAMBLE,
    CodexCliProvider,
    DeviceLogin,
    _find_windows_binary,
    codex_logout,
    codex_status,
)

FAKE = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "codex-home"
    path.mkdir()
    return path


def set_mode(home: Path, **config: object) -> None:
    (home / "fake_mode.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def calls(home: Path) -> list[dict]:
    path = home / "fake_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def provider(home: Path, tmp_path: Path, model: str = "", **params: object) -> CodexCliProvider:
    return CodexCliProvider(
        command=FAKE,
        home=home,
        model=model,
        params=ProfileParams.model_validate(params),
        temp_root=tmp_path / "tmp",
    )


def test_exec_arguments_prompt_and_result(home: Path, tmp_path: Path) -> None:
    request = AnalysisRequest(
        system="Инструкция администратора", user="[B0001] (п. 1.1) Текст", schema={"x": 1}
    )
    result = provider(
        home, tmp_path, model="gpt-5.5-codex", codex_config=['model_reasoning_effort="high"']
    ).analyze(request)

    assert result.data == {"ok": True, "answer": "готов"}
    assert result.response_format == "codex"
    assert (result.usage.input_tokens, result.usage.cached_input_tokens, result.usage.output_tokens) == (
        1200,
        1000,
        30,
    )
    assert result.usage.cost_rub is None

    [call] = calls(home)
    args = call["args"]
    assert args[0] == "exec"
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--color") + 1] == "never"
    assert args[args.index("--model") + 1] == "gpt-5.5-codex"
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert args[-1] == "-"
    # Схема ответа передаётся файлом, задание — через stdin.
    assert call["stdin"].startswith(PREAMBLE)
    assert "Инструкция администратора" in call["stdin"]
    assert "[B0001] (п. 1.1) Текст" in call["stdin"]
    # Codex работает в пустой папке, которая удаляется после ответа.
    assert call["cwd_files"] == []
    assert Path(args[args.index("--cd") + 1]).resolve() == Path(call["cwd"]).resolve()
    assert not Path(call["cwd"]).exists()
    assert list((tmp_path / "tmp").iterdir()) == []


def test_model_flag_is_omitted_without_model(home: Path, tmp_path: Path) -> None:
    provider(home, tmp_path).analyze(PING_REQUEST)
    assert "--model" not in calls(home)[0]["args"]


def test_environment_hides_application_secrets(home: Path, tmp_path: Path, monkeypatch) -> None:
    for name in ("APP_SECRET_KEY", "ENCRYPTION_KEY", "DATABASE_URL", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128")
    provider(home, tmp_path).analyze(PING_REQUEST)
    env = set(calls(home)[0]["env"])
    assert {"APP_SECRET_KEY", "ENCRYPTION_KEY", "DATABASE_URL", "OPENAI_API_KEY", "CODEX_API_KEY"}.isdisjoint(
        env
    )
    assert {"CODEX_HOME", "HTTPS_PROXY"} <= env


def test_answer_falls_back_to_last_agent_message(home: Path, tmp_path: Path) -> None:
    set_mode(home, mode="no_answer_file", answer='```json\n{"ok": true, "answer": "готов"}\n```')
    assert provider(home, tmp_path).analyze(PING_REQUEST).data == {"ok": True, "answer": "готов"}


def test_not_json_answer(home: Path, tmp_path: Path) -> None:
    set_mode(home, answer="Не могу ответить")
    with pytest.raises(ProviderResponseError, match="JSON"):
        provider(home, tmp_path).analyze(PING_REQUEST)


def test_not_logged_in(home: Path, tmp_path: Path) -> None:
    set_mode(home, mode="not_logged_in")
    with pytest.raises(ProviderConfigError, match="codex-login") as error:
        provider(home, tmp_path).analyze(PING_REQUEST)
    assert not error.value.retryable


def test_usage_limit_is_not_retried(home: Path, tmp_path: Path) -> None:
    set_mode(home, mode="usage_limit")
    with pytest.raises(ProviderUnavailableError, match="лимит подписки") as error:
        provider(home, tmp_path).analyze(PING_REQUEST)
    assert not error.value.retryable
    assert "usage limit" in (error.value.detail or "")


def test_timeout_kills_codex(home: Path, tmp_path: Path) -> None:
    set_mode(home, mode="sleep")
    slow = CodexCliProvider(
        command=FAKE,
        home=home,
        model="",
        # Минимальный лимит в настройках — 10 с; для теста задаём 2 с в обход проверки.
        params=ProfileParams.model_construct(**{**ProfileParams().model_dump(), "timeout_seconds": 2}),
        temp_root=tmp_path / "tmp",
    )
    with pytest.raises(ProviderUnavailableError, match="не ответил") as error:
        slow.analyze(PING_REQUEST)
    assert error.value.retryable


def test_missing_binary(home: Path, tmp_path: Path) -> None:
    missing = CodexCliProvider(
        command=[str(tmp_path / "no-codex")], home=home, model="", params=ProfileParams(), temp_root=tmp_path
    )
    with pytest.raises(ProviderConfigError, match="INSTALL_CODEX"):
        missing.analyze(PING_REQUEST)


def test_russia_only_is_rejected(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigError, match="США"):
        provider(home, tmp_path, russia_only=True)


def test_check_requires_login(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigError, match="нет входа"):
        provider(home, tmp_path).check()
    (home / "auth.json").write_text("{}", encoding="utf-8")
    report = provider(home, tmp_path).check()
    assert report.result.data["ok"] is True
    assert report.notes == ["Logged in using ChatGPT"]


def test_device_login_status_and_logout(home: Path) -> None:
    assert not codex_status(FAKE, home).logged_in
    login = DeviceLogin(FAKE, home)
    login.start()
    assert login.wait_for_code(10)
    assert login.url == "https://auth.openai.com/codex/device"
    assert login.code == "ABCD-12345"
    assert login.wait(10)
    status = codex_status(FAKE, home)
    assert status.logged_in
    assert status.message == "Logged in using ChatGPT"
    codex_logout(FAKE, home)
    assert not codex_status(FAKE, home).logged_in


def test_device_login_failure(home: Path) -> None:
    set_mode(home, mode="login_fail")
    login = DeviceLogin(FAKE, home)
    login.start()
    assert login.wait_for_code(10)
    assert not login.wait(10)
    assert "Device code expired" in login.lines


def test_windows_native_binary_is_found_next_to_npm_shim(tmp_path: Path) -> None:
    shim = tmp_path / "npm" / "codex.cmd"
    package = tmp_path / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64"
    binary = package / "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    shim.write_text("@echo off", encoding="utf-8")
    assert _find_windows_binary(shim) == binary
    assert _find_windows_binary(tmp_path / "elsewhere" / "codex.cmd") is None
