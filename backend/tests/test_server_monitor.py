"""Сигналы о работе сервера: deploy/server/contract-review-monitor.sh на подменённых командах.

Скрипт написан для Linux (bash, coreutils) и проверяется там: в Docker с примонтированным репозиторием
(README, «Тесты в Docker»). На Windows и в образе, где папки deploy/ нет, тесты пропускаются. Скрипт пишет
только в каталоги теста: настройки, журнал копии и корень для install задаются переменными окружения.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "server" / "contract-review-monitor.sh"
if not SCRIPT.is_file():
    pytest.skip("deploy/server нет в образе Docker", allow_module_level=True)
if os.name == "nt":
    pytest.skip("скрипт сервера проверяется в Linux (README, «Тесты в Docker»)", allow_module_level=True)

SITE_PING = "https://hc-ping.com/11111111-1111-4111-8111-111111111111"
BACKUP_PING = "https://hc-ping.com/22222222-2222-4222-8222-222222222222"
SITE_URL = "https://203.0.113.10/api/health"

# Подмены команд. Каждая пишет свой вызов в журнал FAKE_CALLS одной строкой JSON.
FAKE_CURL = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"cmd": "curl", "args": args}, ensure_ascii=False) + "\\n")
if "-w" in args:
    sys.stdout.write(os.environ.get("FAKE_SITE_BODY", "") + "\\n" + os.environ.get("FAKE_SITE_CODE", "200"))
    sys.exit(0)
sys.exit(int(os.environ.get("FAKE_PING_EXIT", "0")))
"""
FAKE_MOUNTPOINT = """#!/usr/bin/env python3
import os, sys
sys.exit(0 if os.path.exists(os.path.join(os.environ["FAKE_STATE"], "disk-open")) else 1)
"""
FAKE_SYSTEMCTL = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"cmd": "systemctl", "args": args}, ensure_ascii=False) + "\\n")
if args[:1] == ["is-active"]:
    sys.exit(0 if os.path.exists(os.path.join(os.environ["FAKE_STATE"], "docker-active")) else 3)
"""
FAKE_BACKUP = """import os, sys
with open(os.path.join(os.environ["FAKE_STATE"], "backup-ran"), "w", encoding="utf-8") as mark:
    mark.write(" ".join(sys.argv[1:]))
print("$ docker compose exec -T db pg_dump --username contract_review")
print("$ age --encrypt --recipient age1" + "q" * 58)
DONE = "Готово: backups/contract-review-2026-10-21_030000.dump.age (27 КБ)."
print(os.environ.get("FAKE_BACKUP_OUTPUT", DONE))
sys.exit(int(os.environ.get("FAKE_BACKUP_EXIT", "0")))
"""


class Server:
    """Каталог теста вместо сервера: подменённые команды, файл настроек, папка app и журнал копии."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.state.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, text in (
            ("curl", FAKE_CURL),
            ("mountpoint", FAKE_MOUNTPOINT),
            ("systemctl", FAKE_SYSTEMCTL),
        ):
            command = self.bin / name
            command.write_text(text, encoding="utf-8")
            command.chmod(0o755)
        self.app = root / "app"
        (self.app / "deploy" / "scripts").mkdir(parents=True)
        (self.app / "deploy" / "scripts" / "backup.py").write_text(FAKE_BACKUP, encoding="utf-8")
        self.conf = root / "contract-review-monitor.conf"
        self.write_conf(f"SITE_URL={SITE_URL}\nSITE_PING={SITE_PING}\nBACKUP_PING={BACKUP_PING}\n")
        self.calls_file = root / "calls.jsonl"
        self.backup_log = root / "contract-review-backup.log"

    def write_conf(self, text: str) -> None:
        self.conf.write_text(text, encoding="utf-8", newline="")

    def set(self, *, disk_open: bool = True, docker_active: bool = True) -> None:
        for name, on in (("disk-open", disk_open), ("docker-active", docker_active)):
            path = self.state / name
            if on:
                path.touch()
            else:
                path.unlink(missing_ok=True)

    def run(self, *args: str, use_conf: bool = True, **env: str) -> subprocess.CompletedProcess[str]:
        # Переменные CONTRACT_REVIEW_* того, кто запускает тесты, не должны направить скрипт
        # в настоящие /etc и /var.
        environment = {
            name: value for name, value in os.environ.items() if not name.startswith("CONTRACT_REVIEW_")
        }
        environment.update(
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            FAKE_CALLS=str(self.calls_file),
            FAKE_STATE=str(self.state),
            CONTRACT_REVIEW_DISK_MOUNT=str(self.root / "mount"),
            CONTRACT_REVIEW_MONITOR_APP=str(self.app),
            CONTRACT_REVIEW_MONITOR_BACKUP_LOG=str(self.backup_log),
        )
        if use_conf:
            environment["CONTRACT_REVIEW_MONITOR_CONF"] = str(self.conf)
        environment.update(env)
        # Скрипт из репозитория, bash — из PATH контейнера, аргументы задаёт тест.
        return subprocess.run(  # noqa: S603
            ["bash", str(SCRIPT), *args],  # noqa: S607
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )

    def calls(self, command: str) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        lines = self.calls_file.read_text(encoding="utf-8").splitlines()
        return [entry["args"] for entry in map(json.loads, lines) if entry["cmd"] == command]

    def signals(self) -> list[tuple[str, str]]:
        """Отправленные сигналы: адрес и текст. Запрос к сайту (с -w) сигналом не считается."""
        result = []
        for args in self.calls("curl"):
            if "-w" in args:
                continue
            result.append((args[-1], args[args.index("--data-raw") + 1]))
        return result

    def site_requests(self) -> list[list[str]]:
        return [args for args in self.calls("curl") if "-w" in args]


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


def test_site_that_answers_sends_success(server: Server) -> None:
    server.set()
    result = server.run("site", FAKE_SITE_BODY='{"status":"ok"}')
    assert result.returncode == 0, result.stderr
    assert server.signals() == [(SITE_PING, "ok")]
    request = server.site_requests()[0]
    # Сертификат внутреннего центра Caddy не проверяется, иначе проверка по IP всегда падала бы.
    assert "-sk" in request and request[-1] == SITE_URL


def test_closed_disk_is_a_failure_with_reason(server: Server) -> None:
    server.set(disk_open=False)
    result = server.run("site")
    assert result.returncode == 0
    [(url, text)] = server.signals()
    assert url == f"{SITE_PING}/fail" and "contract-review-disk unlock" in text
    assert server.site_requests() == []


@pytest.mark.parametrize(
    ("docker_active", "code", "body", "reason"),
    [
        (False, "200", '{"status":"ok"}', "Docker не запущен"),
        (True, "502", "", "код 502"),
        (True, "200", '{"status":"degraded"}', "код 200"),
        (True, "000", "", "код 000"),
    ],
)
def test_short_outage_is_only_logged(
    server: Server, docker_active: bool, code: str, body: str, reason: str
) -> None:
    # Сразу после unlock сайт поднимается минуту: такой сбой не должен будить. Сигнала «работает» нет,
    # и если сбой затянется, Healthchecks.io сам сообщит о нём после запаса времени.
    server.set(docker_active=docker_active)
    result = server.run("site", FAKE_SITE_CODE=code, FAKE_SITE_BODY=body)
    assert result.returncode == 0
    [(url, text)] = server.signals()
    assert url == f"{SITE_PING}/log" and reason in text


def test_backup_reports_exit_code_and_result_without_commands(server: Server) -> None:
    server.set()
    result = server.run("backup")
    assert result.returncode == 0, result.stderr
    signals = server.signals()
    assert [url for url, _ in signals] == [f"{BACKUP_PING}/start", f"{BACKUP_PING}/0"]
    text = signals[1][1]
    assert "Готово: backups/contract-review-2026-10-21_030000.dump.age" in text
    # Строки с командами — с открытым ключом age — наружу не уходят, а в журнале копии остаются.
    assert "age1" not in text and "$ docker" not in text
    log = server.backup_log.read_text(encoding="utf-8")
    assert log.startswith("== ") and "$ age --encrypt" in log and "Готово:" in log
    assert (server.state / "backup-ran").read_text(encoding="utf-8") == "--keep 7"


def test_failed_backup_reports_its_code(server: Server) -> None:
    server.set()
    result = server.run("backup", FAKE_BACKUP_EXIT="1", FAKE_BACKUP_OUTPUT="Копия не создана: ошибка.")
    assert result.returncode == 1
    url, text = server.signals()[-1]
    assert url == f"{BACKUP_PING}/1" and "Копия не создана" in text


def test_backup_with_closed_disk_is_not_attempted(server: Server) -> None:
    server.set(disk_open=False)
    result = server.run("backup")
    assert result.returncode == 1
    assert [url for url, _ in server.signals()] == [f"{BACKUP_PING}/start", f"{BACKUP_PING}/fail"]
    assert not (server.state / "backup-ran").exists()
    assert "диск закрыт" in server.backup_log.read_text(encoding="utf-8")


def test_test_command_sends_a_failure_to_check_mail(server: Server) -> None:
    result = server.run("test")
    assert result.returncode == 0
    [(url, text)] = server.signals()
    assert url == f"{SITE_PING}/fail" and "это не сбой" in text


def test_unsent_signal_does_not_reveal_its_address(server: Server) -> None:
    server.set()
    result = server.run("site", FAKE_SITE_BODY='{"status":"ok"}', FAKE_PING_EXIT="7")
    assert result.returncode == 0
    assert "не отправлен" in result.stderr
    assert "11111111" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "conf",
    [
        f"SITE_URL={SITE_URL}\nSITE_PING={SITE_PING}\n",
        f"SITE_URL={SITE_URL}\nSITE_PING={SITE_PING}\nBACKUP_PING=hc-ping.com/22222222\n",
        f"SITE_URL={SITE_URL}\nSITE_PING={SITE_PING}\nBACKUP_PING=\n",
    ],
)
def test_missing_or_bad_address_stops_with_message(server: Server, conf: str) -> None:
    server.write_conf(conf)
    server.set()
    result = server.run("backup")
    assert result.returncode == 1
    assert "BACKUP_PING" in result.stderr
    assert server.signals() == [] and not (server.state / "backup-ran").exists()


def test_settings_saved_in_windows_are_read(server: Server) -> None:
    server.write_conf(f'SITE_URL="{SITE_URL}"\r\nSITE_PING={SITE_PING}/ \r\nBACKUP_PING={BACKUP_PING}\r\n')
    server.set()
    result = server.run("site", FAKE_SITE_BODY='{"status":"ok"}')
    assert result.returncode == 0, result.stderr
    assert server.signals() == [(SITE_PING, "ok")]
    assert server.site_requests()[0][-1] == SITE_URL


def test_missing_settings_file_is_reported(server: Server) -> None:
    result = server.run("site", CONTRACT_REVIEW_MONITOR_CONF=str(server.root / "nowhere.conf"))
    assert result.returncode == 1
    assert "нет файла настроек" in result.stderr


def test_unknown_command_prints_usage(server: Server) -> None:
    result = server.run("restart")
    assert result.returncode == 2
    assert "contract-review-monitor install" in result.stdout


def test_install_puts_script_timer_and_cron_in_place(server: Server) -> None:
    root = server.root / "root"
    conf = root / "etc" / "contract-review-monitor.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        f"SITE_URL={SITE_URL}\nSITE_PING={SITE_PING}\nBACKUP_PING={BACKUP_PING}\n", encoding="utf-8"
    )
    conf.chmod(0o644)
    server.set()
    result = server.run(
        "install", use_conf=False, CONTRACT_REVIEW_MONITOR_ROOT=str(root), FAKE_SITE_BODY='{"status":"ok"}'
    )
    assert result.returncode == 0, result.stderr

    installed = root / "usr" / "local" / "sbin" / "contract-review-monitor"
    assert installed.read_bytes() == SCRIPT.read_bytes()
    assert installed.stat().st_mode & 0o777 == 0o755
    assert conf.stat().st_mode & 0o777 == 0o600
    units = root / "etc" / "systemd" / "system"
    assert "ExecStart=/usr/local/sbin/contract-review-monitor site" in (
        units / "contract-review-monitor.service"
    ).read_text(encoding="utf-8")
    timer = (units / "contract-review-monitor.timer").read_text(encoding="utf-8")
    assert "OnBootSec=3min" in timer and "OnUnitActiveSec=5min" in timer and "WantedBy=timers.target" in timer
    # Строка cron.d — с полем пользователя и переводом строки в конце, иначе cron её не выполнит.
    cron = root / "etc" / "cron.d" / "contract-review-backup"
    text = cron.read_text(encoding="utf-8")
    assert text.endswith("\n") and "0 3 * * * root /usr/local/sbin/contract-review-monitor backup\n" in text
    assert cron.stat().st_mode & 0o777 == 0o644
    assert ["daemon-reload"] in server.calls("systemctl")
    assert ["enable", "--now", "contract-review-monitor.timer"] in server.calls("systemctl")
    # Сразу после установки — первый сигнал, чтобы проверка в Healthchecks.io стала зелёной.
    assert server.signals() == [(SITE_PING, "ok")]


def test_install_without_settings_creates_template_and_stops(server: Server) -> None:
    root = server.root / "root"
    result = server.run("install", use_conf=False, CONTRACT_REVIEW_MONITOR_ROOT=str(root))
    assert result.returncode == 1
    assert "впишите адреса" in result.stderr
    conf = root / "etc" / "contract-review-monitor.conf"
    assert conf.stat().st_mode & 0o777 == 0o600
    assert "SITE_PING=\n" in conf.read_text(encoding="utf-8")
    assert not (root / "etc" / "systemd").exists() and not (root / "etc" / "cron.d").exists()
    assert server.signals() == []


def test_script_has_unix_line_endings() -> None:
    # С переводами строк Windows bash на сервере скрипт не выполнит (.gitattributes: *.sh — LF).
    assert b"\r\n" not in SCRIPT.read_bytes()
