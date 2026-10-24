"""Резервная копия contract-review без договоров (documentation/DEPLOYMENT.md).

    python deploy/scripts/backup.py                         копия в папку backups/ в корне репозитория
    python deploy/scripts/backup.py --out D:/contract-review-backups  в другую папку
    python deploy/scripts/backup.py --keep 7                оставить 7 последних копий в папке
    python deploy/scripts/backup.py --dry-run               только показать команды

Копия — дамп базы PostgreSQL (pg_dump, формат custom): пользователи, версии промпта, подключения ИИ
с зашифрованными ключами, данные организации с подписью и печатью, шаблон протокола, настройки, журнал.
Данных проверок (таблицы checks и jobs) и сессий в копии нет: договоры не хранятся дольше срока жизни
проверки. Секретов из .env в копии тоже нет — .env хранится отдельно: без ENCRYPTION_KEY
ключи подключений ИИ из копии не расшифровать.

Если в .env задан BACKUP_AGE_RECIPIENT — открытый ключ age, — копия шифруется им и получает
окончание .dump.age. Открытый дамп при этом на диск не пишется. Расшифровать копию можно только закрытым
ключом, а он хранится у владельца, не на сервере.

Нужен запущенный сервис db (docker compose up -d), для шифрования — команда age.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from _compose import (
    AGE_MAGIC,
    AGE_RECIPIENT_RE,
    DB_NAME,
    DB_SERVICE,
    DB_USER,
    DUMP_MAGIC,
    REPO,
    ComposeError,
    age,
    env_value,
    require_age,
    run,
    show,
    utf8_output,
)

# Таблицы, данные которых в копию не входят: структура остаётся, строк нет.
EXCLUDED_DATA = ("checks", "jobs", "sessions")
PREFIX = "contract-review-"
SUFFIX = ".dump"
ENCRYPTED_SUFFIX = SUFFIX + ".age"
DEFAULT_DIR = REPO / "backups"
RECIPIENT_VARIABLE = "BACKUP_AGE_RECIPIENT"


def dump_args() -> list[str]:
    args = [
        "exec",
        "-T",
        DB_SERVICE,
        "pg_dump",
        "--username",
        DB_USER,
        "--dbname",
        DB_NAME,
        "--format",
        "custom",
        "--no-owner",
    ]
    for table in EXCLUDED_DATA:
        args += ["--exclude-table-data", table]
    return args


def recipients() -> list[str]:
    """Открытые ключи age из BACKUP_AGE_RECIPIENT (несколько — через пробел). Пусто — копия не шифруется."""
    keys = env_value(RECIPIENT_VARIABLE).replace(",", " ").split()
    for key in keys:
        if key.upper().startswith("AGE-SECRET-KEY-"):
            raise ComposeError(
                f"В {RECIPIENT_VARIABLE} записан закрытый ключ age. Там нужен открытый (age1…), "
                "а закрытый уберите с этой машины: он хранится у владельца."
            )
        if not AGE_RECIPIENT_RE.match(key):
            raise ComposeError(f"{RECIPIENT_VARIABLE}: «{key[:16]}…» — не открытый ключ age (age1…).")
    return keys


def encrypt_args(keys: list[str]) -> list[str]:
    args = ["--encrypt"]
    for key in keys:
        args += ["--recipient", key]
    return args


def backup_path(out_dir: Path, now: datetime, *, encrypted: bool = False) -> Path:
    suffix = ENCRYPTED_SUFFIX if encrypted else SUFFIX
    return out_dir / f"{PREFIX}{now:%Y-%m-%d_%H%M%S}{suffix}"


def write_private(path: Path, data: bytes) -> None:
    """Записать файл, который читает только владелец (в Linux — права 600)."""
    path.unlink(missing_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def make_backup(out_dir: Path, *, keep: int = 0, dry_run: bool = False, now: datetime | None = None) -> Path:
    """Сделать копию и вернуть путь к ней. Недописанный файл не остаётся ни при какой ошибке."""
    keys = recipients()
    target = backup_path(out_dir, now or datetime.now(), encrypted=bool(keys))
    if dry_run:
        run(dump_args(), dry_run=True)
        if keys:
            show("age", encrypt_args(keys))
        return target
    if keys:
        require_age()  # без age копию не сохранить: сообщить об этом до pg_dump
    # Дамп держится в памяти: в нём нет договоров, он небольшой, а открытым на диск не попадает.
    dump = run(dump_args(), capture=True)
    if not dump.startswith(DUMP_MAGIC):
        raise ComposeError("pg_dump не вернул дамп базы: копия не создана.")
    if keys:
        dump = age(encrypt_args(keys), dump)
        if not dump.startswith(AGE_MAGIC):
            raise ComposeError("age не вернула зашифрованный файл: копия не создана.")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        write_private(partial, dump)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    prune(out_dir, keep)
    return target


def copies(out_dir: Path) -> list[Path]:
    """Копии в папке, от старой к новой: имена содержат дату, поэтому сортируются по времени."""
    return sorted(
        path for path in out_dir.glob(f"{PREFIX}*") if path.name.endswith((SUFFIX, ENCRYPTED_SUFFIX))
    )


def prune(out_dir: Path, keep: int) -> list[Path]:
    """Оставить keep последних копий, зашифрованных и нет вместе (0 — не удалять)."""
    if keep <= 0:
        return []
    removed = copies(out_dir)[:-keep]
    for old in removed:
        old.unlink()
    return removed


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} МБ"
    return f"{max(1, round(size / 1024))} КБ"


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    parser = argparse.ArgumentParser(description="Резервная копия contract-review без договоров")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_DIR, help="папка для копий (по умолчанию backups/)"
    )
    parser.add_argument("--keep", type=int, default=0, help="сколько последних копий оставить; 0 — все")
    parser.add_argument("--dry-run", action="store_true", help="только показать команды")
    args = parser.parse_args(argv)
    try:
        target = make_backup(args.out, keep=args.keep, dry_run=args.dry_run)
    except ComposeError as exc:
        print(f"Копия не создана: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0
    print(f"Готово: {target} ({human_size(target.stat().st_size)}).")
    if target.name.endswith(ENCRYPTED_SUFFIX):
        print("Копия зашифрована (age): расшифровать её можно только закрытым ключом владельца.")
    else:
        print("В копии подпись и печать организации — храните её так же бережно, как сами картинки.")
        print(f"Копия не зашифрована: в .env не задан {RECIPIENT_VARIABLE} (DEPLOYMENT.md, раздел 5).")
    print("Файл .env храните отдельно: без него зашифрованные ключи ИИ из копии не прочитать.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
