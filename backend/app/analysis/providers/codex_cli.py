"""Подключение через Codex CLI по подписке ChatGPT.

Для работы — когда системой пользуется один менеджер, вход в его аккаунт ChatGPT или аккаунт компании,
а сервер стоит не в России. В остальных случаях — только для разработки и тестов.

Каждое задание — отдельный запуск `codex exec` в пустой временной папке:
- песочница только на чтение, без сохранения сессий на диск (--ephemeral);
- без пользовательского config.toml и правил (--ignore-user-config, --ignore-rules), чтобы результат
  не зависел от настроек машины;
- ответ строго по JSON Schema (--output-schema), последнее сообщение — в файл (-o);
- задание подаётся через stdin, события — JSON Lines в stdout (--json).

Вход в аккаунт хранится в CODEX_HOME (DATA_DIR/codex). Процесс получает только нужные переменные
окружения: секреты приложения (ключ базы, APP_SECRET_KEY, ENCRYPTION_KEY) агенту не видны.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analysis.providers.base import (
    PING_REQUEST,
    AnalysisRequest,
    AnalysisResult,
    CheckReport,
    ProfileParams,
    ProviderConfigError,
    ProviderError,
    ProviderUnavailableError,
    Usage,
    parse_json_object,
)
from app.core.process import kill_tree, run_with_timeout

LOGIN_HINT = (
    "Выполните вход: python -m app.cli codex-login "
    "(в Docker — docker compose exec app python -m app.cli codex-login)."
)
NOT_INSTALLED = "Codex CLI не установлен: соберите образ с INSTALL_CODEX=true или укажите путь в CODEX_BIN."

# Переменные окружения, которые получает Codex. Остальные (DATABASE_URL, APP_SECRET_KEY, ENCRYPTION_KEY,
# OPENAI_API_KEY, CODEX_API_KEY) не передаются.
ALLOWED_ENV = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    # Windows: без них не запускаются программы и не находится профиль пользователя.
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PATHEXT",
    "COMSPEC",
    "PROCESSOR_ARCHITECTURE",
}

PREAMBLE = (
    "Это задача анализа текста. Всё нужное — ниже. Не запускай команды, не читай и не создавай файлы: "
    "сразу дай ответ по схеме."
)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s\"'<>]+")
_CODE = re.compile(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]{4,})+\b")


def codex_env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items() if key in ALLOWED_ENV or key.upper() in ALLOWED_ENV
    }
    env["CODEX_HOME"] = str(home)
    env["NO_COLOR"] = "1"
    return env


def resolve_codex_command(codex_bin: str) -> list[str]:
    """Команда запуска Codex. На Windows npm ставит обёртку codex.cmd, а запускать нужно codex.exe."""
    path = shutil.which(codex_bin) or codex_bin
    if sys.platform == "win32" and path.lower().endswith((".cmd", ".bat", ".ps1")):
        native = _find_windows_binary(Path(path))
        if native is not None:
            return [str(native)]
    return [path]


def _find_windows_binary(shim: Path) -> Path | None:
    packages = shim.parent / "node_modules" / "@openai"
    patterns = (
        "codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe",
        "codex-win32-*/vendor/*/bin/codex.exe",
        "codex/vendor/*/bin/codex.exe",
    )
    for pattern in patterns:
        found = sorted(packages.glob(pattern))
        if found:
            return found[0]
    return None


def _clean(text: str) -> str:
    return _ANSI.sub("", text).strip()


@dataclass
class _Events:
    usage: dict[str, Any] | None = None
    last_message: str | None = None
    errors: list[str] = field(default_factory=list)
    failed: bool = False


def _parse_events(stdout: bytes) -> _Events:
    events = _Events()
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "turn.completed" and isinstance(event.get("usage"), dict):
            events.usage = event["usage"]
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                events.last_message = item["text"]
        elif kind == "error":
            events.errors.append(str(event.get("message") or ""))
        elif kind == "turn.failed":
            events.failed = True
            error = event.get("error")
            events.errors.append(str(error.get("message") if isinstance(error, dict) else error or ""))
    return events


def _classify(returncode: int, text: str, model: str) -> ProviderError:
    lowered = text.lower()
    detail = text[-1000:] or None
    if any(word in lowered for word in ("not logged in", "login", "unauthorized", "401", "token expired")):
        return ProviderConfigError(f"Codex: нет входа в аккаунт ChatGPT или вход истёк. {LOGIN_HINT}", detail)
    if any(word in lowered for word in ("usage limit", "rate limit", "429", "quota")):
        # Окно подписки — часы, повтор через минуту не поможет.
        return ProviderUnavailableError(
            "Codex: исчерпан лимит подписки ChatGPT. Повторите позже.", detail, retryable=False
        )
    if (
        model
        and "model" in lowered
        and any(word in lowered for word in ("not supported", "does not exist", "unknown"))
    ):
        return ProviderConfigError(f"Codex: модель «{model}» недоступна для этого аккаунта.", detail)
    return ProviderUnavailableError(f"Codex завершился с ошибкой (код {returncode}).", detail)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


class CodexCliProvider:
    kind = "codex_cli"

    def __init__(
        self,
        *,
        command: Sequence[str],
        home: Path,
        model: str,
        params: ProfileParams,
        temp_root: Path | None = None,
    ) -> None:
        if params.russia_only:
            raise ProviderConfigError(
                "Codex работает через серверы OpenAI в США: «Только серверы в РФ» недоступна."
            )
        self.command = list(command)
        self.home = home
        self.model = model
        self.params = params
        self.temp_root = temp_root

    def _exec_args(self, workdir: Path, schema_path: Path, answer_path: Path) -> list[str]:
        args = [
            *self.command,
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--cd",
            str(workdir),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(answer_path),
        ]
        if self.model:
            args += ["--model", self.model]
        for item in self.params.codex_config:
            args += ["--config", item]
        args.append("-")
        return args

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        prompt = f"{PREAMBLE}\n\n{request.system}\n\n{request.user}"
        self.home.mkdir(parents=True, exist_ok=True)
        if self.temp_root is not None:
            self.temp_root.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="codex-", dir=self.temp_root) as tmp:
            root = Path(tmp)
            workdir = root / "work"
            workdir.mkdir()
            schema_path = root / "schema.json"
            schema_path.write_text(json.dumps(request.schema, ensure_ascii=False), encoding="utf-8")
            answer_path = root / "answer.json"
            try:
                result = run_with_timeout(
                    self._exec_args(workdir, schema_path, answer_path),
                    timeout=self.params.timeout_seconds,
                    input_bytes=prompt.encode("utf-8"),
                    env=codex_env(self.home),
                    cwd=workdir,
                )
            except FileNotFoundError as exc:
                raise ProviderConfigError(NOT_INSTALLED) from exc
            except subprocess.TimeoutExpired as exc:
                raise ProviderUnavailableError(
                    f"Codex не ответил за {self.params.timeout_seconds} с. "
                    "Увеличьте лимит времени или повторите позже."
                ) from exc
            events = _parse_events(result.stdout)
            if result.returncode != 0 or events.failed:
                stderr = _clean(result.stderr.decode("utf-8", errors="replace"))
                raise _classify(result.returncode, "\n".join([*events.errors, stderr]).strip(), self.model)
            if answer_path.is_file():
                text = answer_path.read_text(encoding="utf-8", errors="replace")
            else:
                text = events.last_message or ""
        usage = events.usage or {}
        return AnalysisResult(
            data=parse_json_object(text),
            usage=Usage(
                input_tokens=_int(usage.get("input_tokens")),
                cached_input_tokens=_int(usage.get("cached_input_tokens")),
                output_tokens=_int(usage.get("output_tokens")),
            ),
            model=self.model or "по умолчанию в Codex",
            response_format="codex",
            duration_seconds=time.monotonic() - started,
        )

    def check(self) -> CheckReport:
        status = codex_status(self.command, self.home)
        if not status.logged_in:
            raise ProviderConfigError(
                f"Codex: нет входа в аккаунт ChatGPT. {LOGIN_HINT}", status.message or None
            )
        result = self.analyze(PING_REQUEST)
        notes = [status.message] if status.message else []
        if result.data.get("ok") is not True:
            notes.append("Модель ответила, но не по схеме проверки: качество ответов стоит проверить.")
        return CheckReport(model_info=None, result=result, notes=notes)


# --- вход в аккаунт --------------------------------------------------------------------------


@dataclass
class CodexStatus:
    logged_in: bool
    message: str


def _run_codex(
    command: Sequence[str], home: Path, args: Sequence[str], timeout: float
) -> subprocess.CompletedProcess:
    home.mkdir(parents=True, exist_ok=True)
    try:
        return run_with_timeout([*command, *args], timeout=timeout, env=codex_env(home))
    except FileNotFoundError as exc:
        raise ProviderConfigError(NOT_INSTALLED) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderUnavailableError("Codex не ответил на команду входа.") from exc


def codex_status(command: Sequence[str], home: Path, timeout: float = 30) -> CodexStatus:
    """`codex login status`: код 0 — вход выполнен."""
    result = _run_codex(command, home, ["login", "status"], timeout)
    output = _clean((result.stdout + result.stderr).decode("utf-8", errors="replace"))
    first_line = output.splitlines()[0] if output else ""
    return CodexStatus(logged_in=result.returncode == 0, message=first_line[:300])


def codex_logout(command: Sequence[str], home: Path, timeout: float = 30) -> None:
    _run_codex(command, home, ["logout"], timeout)


class DeviceLogin:
    """`codex login --device-auth` в фоне: ссылка и код для администратора, затем ожидание входа.

    Администратор открывает ссылку, вводит код и входит в свой аккаунт ChatGPT. Codex сохраняет
    вход в CODEX_HOME и завершается с кодом 0.
    """

    def __init__(self, command: Sequence[str], home: Path) -> None:
        self.command = list(command)
        self.home = home
        self.lines: list[str] = []
        self.url: str | None = None
        self.code: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._ready = threading.Event()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self._process = subprocess.Popen(  # noqa: S603 — аргументы формирует код
                [*self.command, "login", "--device-auth"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=codex_env(self.home),
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except FileNotFoundError as exc:
            raise ProviderConfigError(NOT_INSTALLED) from exc
        self._reader = threading.Thread(target=self._read, name="codex-login", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        for raw in self._process.stdout:
            line = _clean(raw)
            if not line:
                continue
            self.lines.append(line)
            if self.url is None:
                url = _URL.search(line)
                if url:
                    self.url = url.group(0).rstrip(".,)")
            if self.code is None:
                code = _CODE.search(_URL.sub(" ", line))
                if code:
                    self.code = code.group(0)
            if self.url and self.code:
                self._ready.set()
        self._ready.set()

    def wait_for_code(self, timeout: float = 30) -> bool:
        """Дождаться, пока Codex покажет ссылку и код. False — не показал (смотрите self.lines)."""
        self._ready.wait(timeout)
        return self.url is not None and self.code is not None

    def wait(self, timeout: float) -> bool:
        """Дождаться завершения входа. True — вход выполнен."""
        if self._process is None:
            return False
        try:
            returncode = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.cancel()
            return False
        if self._reader is not None:
            self._reader.join(timeout=5)
        return returncode == 0

    def cancel(self) -> None:
        if self._process is not None and self._process.poll() is None:
            kill_tree(self._process)
            self._process.wait(timeout=10)
