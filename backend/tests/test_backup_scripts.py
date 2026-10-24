"""Скрипты резервного копирования deploy/scripts/backup.py и restore.py на подменённом docker compose.

Настоящие pg_dump и pg_restore проверяются копией и восстановлением в Docker (documentation/DEPLOYMENT.md,
разделы 5 и 6). Скрипты запускаются на машине с Docker и в образ не входят, поэтому в контейнере эти тесты
пропускаются.
"""

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "deploy" / "scripts"
if not SCRIPTS.is_dir():
    pytest.skip("deploy/scripts нет в образе Docker", allow_module_level=True)
sys.path.insert(0, str(SCRIPTS))

import _compose  # noqa: E402
import backup  # noqa: E402
import restore  # noqa: E402

DUMP = b"PGDMP\x01\x0f\x00 fake dump"
# Ключи age для тестов: настоящего формата, но придуманные. Подменённая age сверяет закрытый ключ с IDENTITY.
RECIPIENT = "age1" + "q" * 58
IDENTITY = b"# created: 2026-10-20\nAGE-SECRET-KEY-1TESTTESTTESTTEST\n"


def fake_encrypt(data: bytes) -> bytes:
    return _compose.AGE_MAGIC + data[::-1]


class FakeCompose:
    """Подмена subprocess.run для docker compose и age: запоминает команды и их вход, отдаёт вывод.

    age «шифрует» разворотом байтов, а расшифровывает, только если ей дан ключ IDENTITY.
    """

    def __init__(self, fail_on: str | None = None, output: bytes = DUMP) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.fail_on = fail_on
        self.output = output

    def __call__(
        self, command: list[str], *, input: bytes | None, stdout: Any, check: bool, cwd: Path | None = None
    ) -> Any:
        assert check is False
        if command[0] == "age":
            return self.age(command, input, stdout)
        assert command[:2] == ["docker", "compose"] and cwd == _compose.REPO
        args = command[2:]
        self.calls.append((args, input))
        failed = self.fail_on is not None and self.fail_on in args
        output = self.output if stdout == subprocess.PIPE and not failed else None
        return subprocess.CompletedProcess(command, 1 if failed else 0, stdout=output)

    def age(self, command: list[str], data: bytes | None, stdout: Any) -> Any:
        assert stdout == subprocess.PIPE
        args = command[1:]
        self.calls.append((["age", *args], data))
        if self.fail_on == "age":
            return subprocess.CompletedProcess(command, 1, stdout=b"")
        if args[0] == "--encrypt":
            assert data is not None and data.startswith(_compose.DUMP_MAGIC)
            return subprocess.CompletedProcess(command, 0, stdout=fake_encrypt(data))
        assert args[:2] == ["--decrypt", "--identity"]
        identity = data if args[2] == "-" else Path(args[2]).read_bytes()
        encrypted = Path(args[3]).read_bytes()
        if identity != IDENTITY or not encrypted.startswith(_compose.AGE_MAGIC):
            return subprocess.CompletedProcess(command, 1, stdout=b"")
        return subprocess.CompletedProcess(command, 0, stdout=encrypted[len(_compose.AGE_MAGIC) :][::-1])

    def commands(self) -> list[str]:
        return [" ".join(args[:4]) for args, _ in self.calls]


@pytest.fixture(autouse=True)
def own_backups_dir(monkeypatch, tmp_path: Path) -> Path:
    """Копии, которые скрипты делают по умолчанию (backups/), — во временную папку, а не в репозиторий.

    И настройки машины — из своего .env: тесты не зависят от .env разработчика.
    """
    folder = tmp_path / "default-backups"
    monkeypatch.setattr(backup, "DEFAULT_DIR", folder)
    monkeypatch.setattr(restore, "DEFAULT_DIR", folder)
    monkeypatch.setattr(_compose, "ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv(backup.RECIPIENT_VARIABLE, raising=False)
    return folder


@pytest.fixture
def fake(monkeypatch) -> FakeCompose:
    compose = FakeCompose()
    monkeypatch.setattr(_compose, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(_compose, "age_command", lambda: ["age"])
    monkeypatch.setattr(_compose.subprocess, "run", compose)
    return compose


@pytest.fixture
def encrypted(tmp_path: Path) -> str:
    """Копии шифруются: открытый ключ age записан в .env."""
    env = tmp_path / ".env"
    env.write_text(f"LOG_LEVEL=INFO\n{backup.RECIPIENT_VARIABLE}={RECIPIENT}\n", encoding="utf-8")
    return RECIPIENT


def test_backup_excludes_contract_data(fake: FakeCompose, tmp_path: Path) -> None:
    target = backup.make_backup(tmp_path, now=datetime(2026, 10, 17, 3, 0, 0))
    assert target == tmp_path / "contract-review-2026-10-17_030000.dump"
    assert target.read_bytes() == DUMP
    args, _ = fake.calls[0]
    assert args[:4] == ["exec", "-T", "db", "pg_dump"] and "custom" in args
    excluded = [args[index + 1] for index, arg in enumerate(args) if arg == "--exclude-table-data"]
    # Договоры, задачи и сессии в копию не попадают.
    assert excluded == ["checks", "jobs", "sessions"]
    assert list(tmp_path.glob("*.part")) == []


def test_failed_or_empty_dump_leaves_no_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_compose, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(_compose.subprocess, "run", FakeCompose(fail_on="pg_dump"))
    out = tmp_path / "copies"
    with pytest.raises(_compose.ComposeError, match="кодом 1"):
        backup.make_backup(out)
    monkeypatch.setattr(_compose.subprocess, "run", FakeCompose(output=b"error: no such service"))
    with pytest.raises(_compose.ComposeError, match="не вернул дамп"):
        backup.make_backup(out)
    assert not out.exists() or list(out.iterdir()) == []


def test_keep_removes_oldest_copies(fake: FakeCompose, tmp_path: Path) -> None:
    for day in range(1, 5):
        backup.make_backup(tmp_path, keep=3, now=datetime(2026, 10, day, 3, 0, 0))
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "contract-review-2026-10-02_030000.dump",
        "contract-review-2026-10-03_030000.dump",
        "contract-review-2026-10-04_030000.dump",
    ]


def test_dry_run_only_prints(fake: FakeCompose, tmp_path: Path, capsys) -> None:
    assert backup.main(["--out", str(tmp_path / "copies"), "--dry-run"]) == 0
    assert fake.calls == [] and not (tmp_path / "copies").exists()
    assert "$ docker compose exec -T db pg_dump" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="права файлов — в Linux")
def test_backup_is_readable_only_by_owner(fake: FakeCompose, tmp_path: Path) -> None:
    target = backup.make_backup(tmp_path / "copies")
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_encrypted_backup_never_writes_plain_dump(fake: FakeCompose, encrypted: str, tmp_path: Path) -> None:
    out = tmp_path / "copies"
    target = backup.make_backup(out, now=datetime(2026, 10, 21, 3, 0, 0))
    assert target == out / "contract-review-2026-10-21_030000.dump.age"
    assert target.read_bytes() == fake_encrypt(DUMP)
    assert [path.name for path in out.iterdir()] == [target.name]
    assert fake.commands() == ["exec -T db pg_dump", f"age --encrypt --recipient {encrypted}"]
    # Дамп ушёл в age через память, а не через файл.
    assert fake.calls[1][1] == DUMP


def test_encrypted_backup_without_age_or_with_bad_key_is_not_made(
    fake: FakeCompose, encrypted: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    out = tmp_path / "copies"
    env = tmp_path / ".env"
    fake.fail_on = "age"
    with pytest.raises(_compose.ComposeError, match="age завершилась"):
        backup.make_backup(out)
    fake.fail_on = None

    def no_age() -> list[str]:
        raise _compose.ComposeError("Не найдена команда age")

    monkeypatch.setattr(_compose, "age_command", no_age)
    assert backup.main(["--out", str(out)]) == 1
    assert "Не найдена команда age" in capsys.readouterr().err
    calls = len(fake.calls)

    # Ключ проверяется до pg_dump: закрытый ключ вместо открытого — понятная ошибка, а не копия без шифра.
    env.write_text(f"{backup.RECIPIENT_VARIABLE}=AGE-SECRET-KEY-1ABC\n", encoding="utf-8")
    with pytest.raises(_compose.ComposeError, match="закрытый ключ"):
        backup.make_backup(out)
    env.write_text(f"{backup.RECIPIENT_VARIABLE}=age1short\n", encoding="utf-8")
    with pytest.raises(_compose.ComposeError, match="не открытый ключ"):
        backup.make_backup(out)
    assert len(fake.calls) == calls
    assert not out.exists() or list(out.iterdir()) == []


def test_recipient_from_environment_overrides_env_file(
    fake: FakeCompose, encrypted: str, tmp_path: Path, monkeypatch
) -> None:
    second = "age1" + "p" * 58
    monkeypatch.setenv(backup.RECIPIENT_VARIABLE, f"{second}, {encrypted}")
    assert backup.recipients() == [second, encrypted]
    monkeypatch.setenv(backup.RECIPIENT_VARIABLE, "")
    target = backup.make_backup(tmp_path / "copies")
    assert target.name.endswith(".dump") and target.read_bytes() == DUMP


def test_keep_counts_plain_and_encrypted_copies(fake: FakeCompose, tmp_path: Path) -> None:
    out = tmp_path / "copies"
    for day in (1, 2):
        backup.make_backup(out, now=datetime(2026, 10, day, 3, 0, 0))
    (tmp_path / ".env").write_text(f"{backup.RECIPIENT_VARIABLE}={RECIPIENT}\n", encoding="utf-8")
    (out / "contract-review-2026-10-05_030000.dump.age.part").write_bytes(b"unfinished")
    for day in (3, 4):
        backup.make_backup(out, keep=3, now=datetime(2026, 10, day, 3, 0, 0))
    assert sorted(path.name for path in out.iterdir()) == [
        "contract-review-2026-10-02_030000.dump",
        "contract-review-2026-10-03_030000.dump.age",
        "contract-review-2026-10-04_030000.dump.age",
        "contract-review-2026-10-05_030000.dump.age.part",
    ]


def test_restore_sequence(fake: FakeCompose, tmp_path: Path) -> None:
    source = tmp_path / "contract-review-old.dump"
    source.write_bytes(DUMP + b" from the old machine")
    safety_dir = tmp_path / "safety"
    safety = restore.restore(source, safety_dir=safety_dir)
    # Сначала копия текущей базы, потом остановка, очистка схемы, загрузка и запуск.
    assert fake.commands() == [
        "exec -T db pg_dump",
        "stop app worker",
        "up -d --wait db",
        "exec -T db psql",
        "exec -T db pg_restore",
        "up -d",
    ]
    assert safety is not None and safety.parent == safety_dir and safety.read_bytes() == DUMP
    psql_args, _ = fake.calls[3]
    assert psql_args[-1] == restore.RESET_SCHEMA
    restore_args, data = fake.calls[4]
    assert "--single-transaction" in restore_args and data == source.read_bytes()


def test_restore_encrypted_copy_with_key_from_stdin(
    fake: FakeCompose, encrypted: str, tmp_path: Path, own_backups_dir: Path, monkeypatch
) -> None:
    source = tmp_path / "contract-review-2026-10-21_030000.dump.age"
    source.write_bytes(fake_encrypt(DUMP + b" encrypted"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(IDENTITY)))
    assert restore.main([str(source), "--identity", "-", "--yes"]) == 0
    assert fake.commands() == [
        "age --decrypt --identity -",
        "exec -T db pg_dump",
        f"age --encrypt --recipient {encrypted}",
        "stop app worker",
        "up -d --wait db",
        "exec -T db psql",
        "exec -T db pg_restore",
        "up -d",
    ]
    # Ключ пришёл в age со стандартного ввода, в pg_restore — расшифрованный дамп.
    assert fake.calls[0] == (["age", "--decrypt", "--identity", "-", str(source)], IDENTITY)
    assert fake.calls[6][1] == DUMP + b" encrypted"
    # Копия текущей базы перед восстановлением тоже зашифрована.
    assert [path.name.endswith(".dump.age") for path in own_backups_dir.iterdir()] == [True]


def test_restore_encrypted_copy_with_key_file(fake: FakeCompose, tmp_path: Path) -> None:
    source = tmp_path / "copy.dump.age"
    source.write_bytes(fake_encrypt(DUMP))
    key = tmp_path / "contract-review-backup.key"
    key.write_bytes(IDENTITY)
    restore.restore(source, identity=str(key), safety_dir=tmp_path / "safety")
    # Файл ключа читает сам restore.py и передаёт age со стандартного ввода.
    assert fake.calls[0] == (["age", "--decrypt", "--identity", "-", str(source)], IDENTITY)
    assert fake.calls[-2][1] == DUMP


WINDOWS_KEYS = {
    "Блокнот, UTF-8 с BOM": b"\xef\xbb\xbf" + IDENTITY,
    "CRLF и пробелы по краям": b"  " + IDENTITY.replace(b"\n", b" \r\n"),
    "> в Windows PowerShell 5.1 (UTF-16)": IDENTITY.decode().replace("\n", "\r\n").encode("utf-16"),
    "UTF-16 big-endian": b"\xfe\xff" + IDENTITY.decode().encode("utf-16-be"),
}


@pytest.mark.parametrize("saved", list(WINDOWS_KEYS.values()), ids=list(WINDOWS_KEYS))
def test_key_saved_in_windows_is_read(fake: FakeCompose, tmp_path: Path, monkeypatch, saved: bytes) -> None:
    # Такой ключ age сама не читает («unknown identity type»): restore.py очищает его.
    source = tmp_path / "copy.dump.age"
    source.write_bytes(fake_encrypt(DUMP))
    key = tmp_path / "contract-review-backup.key"
    key.write_bytes(saved)
    assert restore.main([str(source), "--identity", str(key), "--yes"]) == 0
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(saved)))
    assert restore.main([str(source), "--identity", "-", "--yes"]) == 0
    decrypted = [data for args, data in fake.calls if args[:2] == ["age", "--decrypt"]]
    assert decrypted == [IDENTITY, IDENTITY]


def test_not_a_key_is_reported_before_anything_changes(fake: FakeCompose, tmp_path: Path, capsys) -> None:
    source = tmp_path / "copy.dump.age"
    source.write_bytes(fake_encrypt(DUMP))
    public = tmp_path / "public.txt"
    public.write_text(f"# public key: {RECIPIENT}\n", encoding="utf-8")
    assert restore.main([str(source), "--identity", str(public), "--yes"]) == 1
    assert "нет закрытого ключа age" in capsys.readouterr().err
    assert restore.main([str(source), "--identity", str(tmp_path / "missing.key"), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "missing.key не прочитан" in err and "База не изменена" in err
    assert fake.calls == []


def test_encrypted_copy_with_wrong_or_missing_key_changes_nothing(
    fake: FakeCompose, tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "copy.dump.age"
    source.write_bytes(fake_encrypt(DUMP))
    assert restore.main([str(source), "--yes"]) == 1
    assert "--identity" in capsys.readouterr().err
    wrong = tmp_path / "other.key"
    wrong.write_bytes(b"AGE-SECRET-KEY-1OTHER\n")
    assert restore.main([str(source), "--identity", str(wrong), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "age завершилась" in err and "База не изменена" in err
    # Ключ со стандартного ввода — только с --yes: подтверждение прочитать уже неоткуда.
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(IDENTITY)))
    assert restore.main([str(source), "--identity", "-"]) == 1
    assert "--yes" in capsys.readouterr().err
    # Ни одной команды docker compose: система не останавливалась, база не тронута.
    assert [args[0] for args, _ in fake.calls] == ["age"]


def test_restore_dry_run_of_encrypted_copy_needs_no_key(fake: FakeCompose, tmp_path: Path, capsys) -> None:
    source = tmp_path / "copy.dump.age"
    source.write_bytes(fake_encrypt(DUMP))
    assert restore.main([str(source), "--dry-run"]) == 0
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "$ age --decrypt --identity - " in out and "pg_restore" in out


def test_restore_stops_on_error_and_rejects_foreign_files(
    monkeypatch, tmp_path: Path, own_backups_dir: Path, capsys
) -> None:
    monkeypatch.setattr(_compose, "compose_command", lambda: ["docker", "compose"])
    failing = FakeCompose(fail_on="pg_restore")
    monkeypatch.setattr(_compose.subprocess, "run", failing)
    source = tmp_path / "copy.dump"
    source.write_bytes(DUMP)
    assert restore.main([str(source), "--yes"]) == 1
    # Копия текущей базы перед восстановлением — в папке по умолчанию.
    assert len(list(own_backups_dir.glob("contract-review-*.dump"))) == 1
    # После ошибки загрузки система не запускается на пустой базе: команда «up -d» не выполняется.
    assert [args[0] for args, _ in failing.calls][-1] == "exec"
    assert "ещё раз с той же копией" in capsys.readouterr().err

    other = tmp_path / "notes.txt"
    other.write_text("не дамп", encoding="utf-8")
    assert restore.main([str(other), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "не копия" in err and "База не изменена" in err
    assert restore.main([str(tmp_path / "missing.dump"), "--yes"]) == 1


def test_restore_needs_confirmation_without_terminal(fake: FakeCompose, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "copy.dump"
    source.write_bytes(DUMP)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert restore.main([str(source)]) == 1
    assert fake.calls == []


@pytest.mark.parametrize("stop", [EOFError, KeyboardInterrupt])
def test_restore_without_answer_does_nothing(
    fake: FakeCompose, tmp_path: Path, monkeypatch, capsys, stop: type[BaseException]
) -> None:
    # В Windows ввод из NUL выглядит как терминал, а input() сразу получает конец ввода.
    source = tmp_path / "copy.dump"
    source.write_bytes(DUMP)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def no_answer(prompt: str) -> str:
        raise stop

    monkeypatch.setattr("builtins.input", no_answer)
    assert restore.main([str(source)]) == 1
    assert fake.calls == []
    assert "Отменено" in capsys.readouterr().out


def test_size_is_readable() -> None:
    assert backup.human_size(43_686) == "43 КБ"
    assert backup.human_size(100) == "1 КБ"
    assert backup.human_size(5 * 1024 * 1024) == "5.0 МБ"


def test_messages_survive_non_utf8_output(fake: FakeCompose, tmp_path: Path, monkeypatch) -> None:
    # Вывод в файл в Windows (Планировщик заданий, >> backup.log) идёт в кодировке системы: без UTF-8
    # сообщение о готовой копии падало с UnicodeEncodeError, и скрипт завершался с ошибкой.
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    assert backup.main(["--out", str(tmp_path)]) == 0
    stdout.flush()
    assert "Готово" in raw.getvalue().decode("utf-8")


def test_init_env_fills_secrets_and_survives_non_utf8_output(tmp_path: Path, monkeypatch) -> None:
    import init_env

    example = tmp_path / ".env.example"
    example.write_text(
        "APP_SECRET_KEY=change-me\nENCRYPTION_KEY=\nINITIAL_ADMIN_PASSWORD=\nLOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(init_env, "EXAMPLE", example)
    monkeypatch.setattr(init_env, "TARGET", tmp_path / ".env")
    monkeypatch.setattr(sys, "argv", ["init_env.py"])
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    # Раньше сообщение о готовом .env падало на кириллице, если вывод шёл в файл (Windows).
    assert init_env.main() == 0
    stdout.flush()
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines)
    assert values["APP_SECRET_KEY"] != "change-me" and len(values["APP_SECRET_KEY"]) >= 32
    assert values["ENCRYPTION_KEY"] and values["INITIAL_ADMIN_PASSWORD"]
    assert values["LOG_LEVEL"] == "INFO"
    output = raw.getvalue().decode("utf-8")
    assert "Создан" in output
    assert values["INITIAL_ADMIN_PASSWORD"] not in output
