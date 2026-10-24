"""Общее для backup.py и restore.py: вызов docker compose из корня репозитория и шифрование копий (age).

Только стандартная библиотека Python: скрипты запускаются на машине с Docker (Windows, Linux, macOS),
а не в контейнере.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB_SERVICE = "db"
# Пользователь и база заданы в docker-compose.yml (сервис db).
DB_USER = "contract_review"
DB_NAME = "contract_review"
# Первые байты дампа pg_dump в формате custom.
DUMP_MAGIC = b"PGDMP"
# Первая строка файла, зашифрованного age.
AGE_MAGIC = b"age-encryption.org/v1\n"
# Открытый ключ age: «age1» и 58 знаков bech32.
AGE_RECIPIENT_RE = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")
ENV_FILE = REPO / ".env"


class ComposeError(RuntimeError):
    """Команда docker compose не выполнилась. Текст — для человека, который запустил скрипт."""


def utf8_output() -> None:
    """Вывод скрипта — в UTF-8.

    Когда вывод идёт в файл или канал (cron, Планировщик заданий, `>> backup.log`), Python в Windows пишет
    в кодировке системы, и print с кириллицей может упасть уже после того, как копия сделана.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def env_value(name: str) -> str:
    """Настройка машины: из окружения, иначе из .env в корне репозитория. Не задана — пустая строка.

    Окружение важнее .env, как у самого docker compose.
    """
    if name in os.environ:
        return os.environ[name].strip()
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and key.strip() == name:
            return value.strip().strip("\"'")
    return ""


def compose_command() -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        raise ComposeError("Не найдена команда docker. Установите Docker и запустите скрипт ещё раз.")
    return [docker, "compose"]


def age_command() -> list[str]:
    age = shutil.which("age")
    if age is None:
        raise ComposeError(
            "Не найдена команда age, которой шифруются копии. Установите её: в Linux — sudo apt install age, "
            "в Windows — winget install FiloSottile.age."
        )
    return [age]


def require_age() -> None:
    """Проверить, что команда age есть, — до работы, итог которой без неё не сохранить."""
    age_command()


def show(program: str, args: list[str]) -> None:
    print(f"$ {program} " + " ".join(shlex.quote(arg) for arg in args), flush=True)


def run(args: list[str], *, data: bytes | None = None, capture: bool = False, dry_run: bool = False) -> bytes:
    """docker compose <args> в корне репозитория. data — на вход команды; capture — вернуть её вывод.

    При dry_run только печатает команду.
    """
    show("docker compose", args)
    if dry_run:
        return b""
    result = subprocess.run(  # noqa: S603
        [*compose_command(), *args],
        cwd=REPO,
        input=data,
        stdout=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode != 0:
        raise ComposeError(f"docker compose {args[0]} завершилась с кодом {result.returncode}.")
    return result.stdout if capture else b""


def age(args: list[str], data: bytes | None = None) -> bytes:
    """age <args>: data на вход, вывод age — результат. Сообщение об ошибке age печатает сама."""
    show("age", args)
    result = subprocess.run([*age_command(), *args], input=data, stdout=subprocess.PIPE, check=False)  # noqa: S603
    if result.returncode != 0:
        raise ComposeError(f"age завершилась с кодом {result.returncode}.")
    return result.stdout
