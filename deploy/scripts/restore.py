"""Восстановление contract-review из резервной копии (documentation/DEPLOYMENT.md).

    python deploy/scripts/restore.py backups/contract-review-2026-10-17_030000.dump
    python deploy/scripts/restore.py <копия>.dump.age --identity <ключ>   зашифрованная копия
    python deploy/scripts/restore.py <копия>.dump.age --identity - --yes  ключ — со стандартного ввода
    python deploy/scripts/restore.py <копия> --yes        без вопроса (для скриптов)
    python deploy/scripts/restore.py <копия> --dry-run    только показать команды

Что делает:
1. Зашифрованную копию расшифровывает в памяти закрытым ключом age: открытый дамп на диск не пишется.
   Если ключ не подошёл, система не останавливается.
2. Делает копию текущей базы (backups/contract-review-<время>.dump или .dump.age) — на случай, если восстановили не то.
3. Останавливает app и worker, запускает db и ждёт, пока она будет готова.
4. Очищает схему базы и загружает копию (pg_restore).
5. Запускает систему: app применяет миграции, если копия сделана старой версией.

Проверки, которые были в базе, удаляются: в копиях их нет. Пользователи входят заново.
Файл .env должен быть тот же, что на машине, где сделана копия, — иначе ключи подключений ИИ придётся
ввести заново (ENCRYPTION_KEY), а пароль базы взят из нового .env (POSTGRES_PASSWORD).

С --identity - ключ читается со стандартного ввода и на сервере не сохраняется. С компьютера владельца:
    ssh <сервер> "cd <папка> && python3 deploy/scripts/restore.py <копия> --identity - --yes" < <ключ>
Файл ключа может быть сохранён в Windows: с меткой BOM, в UTF-16, с переводами строк CRLF.
"""

import argparse
import codecs
import sys
from pathlib import Path

from _compose import (
    AGE_MAGIC,
    DB_NAME,
    DB_SERVICE,
    DB_USER,
    DUMP_MAGIC,
    ComposeError,
    age,
    run,
    show,
    utf8_output,
)
from backup import DEFAULT_DIR, make_backup

RESET_SCHEMA = "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
# Ключ age со стандартного ввода.
STDIN = "-"
# Начало строки закрытого ключа age.
AGE_IDENTITY_PREFIX = "AGE-SECRET-KEY-1"


def psql_args(sql: str) -> list[str]:
    return [
        "exec",
        "-T",
        DB_SERVICE,
        "psql",
        "--username",
        DB_USER,
        "--dbname",
        DB_NAME,
        "--set",
        "ON_ERROR_STOP=1",
        "--command",
        sql,
    ]


def restore_args() -> list[str]:
    return [
        "exec",
        "-T",
        DB_SERVICE,
        "pg_restore",
        "--username",
        DB_USER,
        "--dbname",
        DB_NAME,
        "--no-owner",
        "--single-transaction",
        "--exit-on-error",
    ]


def is_encrypted(path: Path) -> bool:
    """Зашифрована ли копия. Файл, который не похож на копию backup.py, — ошибка."""
    if not path.is_file():
        raise ComposeError(f"Файл {path} не найден.")
    with path.open("rb") as handle:
        head = handle.read(len(AGE_MAGIC))
    if head.startswith(AGE_MAGIC):
        return True
    if head.startswith(DUMP_MAGIC):
        return False
    raise ComposeError(f"{path} — не копия, сделанная backup.py (нет признака дампа pg_dump или age).")


def decrypt_args(path: Path) -> list[str]:
    # Ключ age получает со стандартного ввода — уже прочитанным и очищенным (identity_bytes).
    return ["--decrypt", "--identity", STDIN, str(path)]


def identity_bytes(raw: bytes) -> bytes:
    """Закрытый ключ age в том виде, который age читает: UTF-8 без метки, строки без пробелов по краям.

    Файл ключа мог быть сохранён в Windows: Блокнот ставит метку BOM, перенаправление > в Windows
    PowerShell 5.1 пишет UTF-16. Такой файл age сама не читает («unknown identity type»).
    """
    try:
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ComposeError("Файл ключа не прочитан: это не текстовый ключ age.") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not any(line.startswith(AGE_IDENTITY_PREFIX) for line in lines):
        raise ComposeError(f"В ключе нет закрытого ключа age (строки {AGE_IDENTITY_PREFIX}…).")
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_dump(path: Path, identity: str | None = None, identity_data: bytes | None = None) -> bytes:
    """Дамп из копии. Зашифрованная расшифровывается в памяти ключом identity.

    identity — путь к файлу ключа или «-»: тогда ключ — identity_data, прочитанный со стандартного ввода.
    """
    if not is_encrypted(path):
        return path.read_bytes()
    if not identity:
        raise ComposeError(
            f"{path} зашифрована (age). Укажите закрытый ключ: --identity <файл ключа> "
            "или --identity - и ключ на стандартный ввод."
        )
    if identity == STDIN:
        raw = identity_data or b""
    else:
        try:
            raw = Path(identity).read_bytes()
        except OSError as exc:
            raise ComposeError(f"Файл ключа {identity} не прочитан: {exc.strerror}.") from exc
    dump = age(decrypt_args(path), identity_bytes(raw))
    if not dump.startswith(DUMP_MAGIC):
        raise ComposeError(f"В {path} после расшифровки нет дампа pg_dump.")
    return dump


def restore(
    path: Path,
    *,
    dump: bytes | None = None,
    identity: str | None = None,
    identity_data: bytes | None = None,
    safety_dir: Path | None = None,
    dry_run: bool = False,
) -> Path | None:
    """Восстановить базу из копии. Возвращает путь к копии, сделанной перед восстановлением.

    dump — уже прочитанный дамп этой копии; иначе он читается (и расшифровывается) здесь, до остановки
    системы: если ключ не подошёл или копия испорчена, система продолжает работать.
    safety_dir — папка для копии текущей базы, по умолчанию backups/.
    """
    if dry_run:
        if is_encrypted(path):
            show("age", decrypt_args(path))
        dump = b""
    elif dump is None:
        dump = load_dump(path, identity, identity_data)
    safety = make_backup(safety_dir or DEFAULT_DIR, dry_run=dry_run)
    run(["stop", "app", "worker"], dry_run=dry_run)
    run(["up", "-d", "--wait", DB_SERVICE], dry_run=dry_run)
    run(psql_args(RESET_SCHEMA), dry_run=dry_run)
    run(restore_args(), data=dump, dry_run=dry_run)
    run(["up", "-d"], dry_run=dry_run)
    return None if dry_run else safety


def confirm(path: Path) -> bool:
    if not sys.stdin.isatty():
        print("Подтвердите восстановление флагом --yes.", file=sys.stderr)
        return False
    print(f"База будет заменена содержимым {path}. Проверки и сессии удалятся, система перезапустится.")
    try:
        answer = input("Продолжить? Введите «да»: ")
    except EOFError:
        # Ввод кончился: в Windows NUL выглядит как терминал (isatty), например у запуска из планировщика.
        print()
        print("Подтвердите восстановление флагом --yes.", file=sys.stderr)
        return False
    except KeyboardInterrupt:
        print()
        return False
    return answer.strip().lower() == "да"


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    parser = argparse.ArgumentParser(description="Восстановить contract-review из резервной копии")
    parser.add_argument("dump", type=Path, help="файл копии (contract-review-….dump или contract-review-….dump.age)")
    parser.add_argument(
        "--identity",
        help="закрытый ключ age для зашифрованной копии: путь к файлу или «-» — со стандартного ввода",
    )
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    parser.add_argument("--dry-run", action="store_true", help="только показать команды")
    args = parser.parse_args(argv)
    try:
        encrypted = is_encrypted(args.dump)
        identity_data = None
        if encrypted and args.identity == STDIN and not args.dry_run:
            if not args.yes:
                # Стандартный ввод занят ключом: ответить на вопрос нечем.
                print("С --identity - подтвердите восстановление флагом --yes.", file=sys.stderr)
                return 1
            identity_data = sys.stdin.buffer.read()
        # Копия читается и расшифровывается до вопроса и до остановки системы.
        dump = None if args.dry_run else load_dump(args.dump, args.identity, identity_data)
    except ComposeError as exc:
        print(f"Восстановление не выполнено: {exc}", file=sys.stderr)
        print("База не изменена, система работает как прежде.", file=sys.stderr)
        return 1
    if not args.dry_run and not args.yes and not confirm(args.dump):
        print("Отменено.")
        return 1
    try:
        safety = restore(args.dump, dump=dump, identity=args.identity, dry_run=args.dry_run)
    except ComposeError as exc:
        print(f"Восстановление не выполнено: {exc}", file=sys.stderr)
        print("Если база уже очищена, запустите восстановление ещё раз с той же копией.", file=sys.stderr)
        return 1
    if safety is not None:
        print(f"Готово. Копия базы до восстановления: {safety}")
        print("Откройте систему и войдите заново: сессии в копию не входят.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
