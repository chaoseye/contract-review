"""Создаёт .env из .env.example и заполняет секреты случайными значениями.

    python deploy/scripts/init_env.py            создать .env, если его нет
    python deploy/scripts/init_env.py --force    пересоздать (старый сохраняется как .env.bak)

Заполняются POSTGRES_PASSWORD, APP_SECRET_KEY, ENCRYPTION_KEY и INITIAL_ADMIN_PASSWORD, если они пустые
или равны заглушке. Пароль первого администратора остаётся только в .env — в консоль он не выводится.
Скрипт использует только стандартную библиотеку Python и работает на Windows, Linux и macOS.
"""

import argparse
import base64
import os
import secrets
import shutil
import sys
from pathlib import Path

from _compose import utf8_output

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / ".env.example"
TARGET = REPO / ".env"
PLACEHOLDERS = {"", "change-me"}


def generators() -> dict[str, str]:
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        # Формат ключа Fernet: 32 случайных байта в base64url.
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(12),
    }


def render(example: str) -> str:
    values = generators()
    lines = []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and not line.lstrip().startswith("#") and key in values and value.strip() in PLACEHOLDERS:
            line = f"{key}={values[key]}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def main() -> int:
    utf8_output()
    parser = argparse.ArgumentParser(description="Создать .env со случайными секретами")
    parser.add_argument("--force", action="store_true", help="пересоздать существующий .env")
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"{TARGET} уже есть. Чтобы пересоздать, запустите с --force.")
        return 1
    if TARGET.exists():
        shutil.copy2(TARGET, TARGET.with_suffix(".bak"))
        print(f"Старый файл сохранён как {TARGET.with_suffix('.bak')}")

    TARGET.write_text(render(EXAMPLE.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    print(f"Создан {TARGET}. Пароль первого администратора — в INITIAL_ADMIN_PASSWORD внутри этого файла.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
