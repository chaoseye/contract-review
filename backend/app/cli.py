"""Служебные команды: `python -m app.cli <команда>`.

wait-db        дождаться доступности базы (при старте контейнера)
migrate        применить миграции
bootstrap      создать первого администратора, если пользователей нет
create-user    создать пользователя (пароль читается из stdin или запрашивается)
set-password   сменить пароль пользователю
extract        прочитать договор и показать блоки с номерами пунктов (для отладки разбора)
protocol       собрать протокол разногласий из JSON в docx и PDF
org-show       данные нашей организации для протокола
org-set        изменить наименование, подписанта, город, реквизиты
org-asset      загрузить подпись, печать или шапку бланка (PNG, JPEG)
llm-*          подключения ИИ: list, add, set, activate, delete, models, test (app/analysis/cli_commands.py)
codex-*        вход в Codex CLI: login, status, logout
"""

import argparse
import getpass
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.analysis import cli_commands as llm_commands
from app.bootstrap import ensure_initial_admin
from app.core.config import BACKEND_DIR, Settings, get_settings
from app.core.db import make_engine, make_sessionmaker, session_scope
from app.core.logging import configure_logging
from app.core.security import hash_password, validate_new_password
from app.models import User, UserRole
from app.models.user import normalize_login

log = logging.getLogger("app.cli")


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # ConfigParser воспринимает % как подстановку, поэтому экранируем.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def wait_db(settings: Settings, timeout: float) -> None:
    engine = make_engine(settings.database_url)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except SQLAlchemyError:
                if time.monotonic() >= deadline:
                    raise
                log.info("база ещё недоступна, жду…")
                time.sleep(2)
    finally:
        engine.dispose()


def migrate(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")


def bootstrap(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            ensure_initial_admin(db, settings)
    finally:
        engine.dispose()


def read_password(prompt: str) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass(prompt)
    if getpass.getpass("Повторите пароль: ") != first:
        raise SystemExit("Пароли не совпадают")
    return first


def create_user(settings: Settings, login: str, name: str, role: str) -> None:
    password = read_password("Пароль: ")
    validate_new_password(password)
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            normalized = normalize_login(login)
            if db.scalar(select(User).where(User.login == normalized)):
                raise SystemExit(f"Пользователь {normalized} уже есть")
            db.add(User(login=normalized, name=name, role=role, password_hash=hash_password(password)))
        print(f"Создан пользователь {normalized} ({role})")
    finally:
        engine.dispose()


def set_password(settings: Settings, login: str) -> None:
    password = read_password("Новый пароль: ")
    validate_new_password(password)
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            user = db.scalar(select(User).where(User.login == normalize_login(login)))
            if user is None:
                raise SystemExit(f"Пользователь {login} не найден")
            user.password_hash = hash_password(password)
            # Смена пароля завершает все сессии пользователя.
            user.sessions.clear()
        print(f"Пароль пользователя {user.login} изменён")
    finally:
        engine.dispose()


def extract(path: Path, as_json: bool, width: int) -> None:
    """Вывод разбора в консоль. Только для ручной проверки: в логи этот текст не попадает."""
    from app.documents.errors import DocumentError
    from app.documents.extract import extract_document

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="contract-review-extract-") as work:
        try:
            # Ход чтения — в stderr: с --json в stdout остаётся только JSON.
            document = extract_document(
                path, Path(work), progress=lambda message: print(f"… {message}", file=sys.stderr)
            )
        except DocumentError as exc:
            raise SystemExit(f"Не удалось прочитать: {exc}") from exc
    if as_json:
        print(json.dumps([block.to_dict() for block in document.blocks], ensure_ascii=False, indent=2))
        return
    print(f"Тип: {document.kind}, страниц: {document.page_count}, OCR: {document.ocr_used}")
    print(f"Блоков: {len(document.blocks)}")
    for warning in document.warnings:
        print(f"! {warning}")
    for block in document.blocks:
        clause = block.clause or "—"
        print(f"{block.id} {block.kind:<9} п. {clause:<8} {block.text[:width]}")
    clauses: list[str] = []
    for block in document.blocks:
        if block.clause and (not clauses or clauses[-1] != block.clause):
            clauses.append(block.clause)
    print("Пункты по порядку:", " ".join(clauses))


def protocol(
    data_path: Path,
    out: Path,
    pdf: Path | None,
    header: Path | None,
    signature: Path | None,
    stamp: Path | None,
) -> None:
    """Собрать протокол из JSON (формат — documentation/PROTOCOL_FORMAT.md, раздел 8)."""
    from app.documents.errors import DocumentError
    from app.protocol.builder_docx import build_docx
    from app.protocol.model import ProtocolData, ProtocolImages
    from app.protocol.pdf import docx_to_pdf

    data = ProtocolData.model_validate_json(data_path.read_text(encoding="utf-8"))
    images = ProtocolImages(
        header=header.read_bytes() if header else None,
        signature=signature.read_bytes() if signature else None,
        stamp=stamp.read_bytes() if stamp else None,
    )
    docx = build_docx(data, images, with_signature=signature is not None, with_stamp=stamp is not None)
    out.write_bytes(docx)
    print(f"Word: {out}")
    if pdf is not None:
        try:
            pdf.write_bytes(docx_to_pdf(docx))
        except DocumentError as exc:
            raise SystemExit(f"PDF не собран: {exc}") from exc
        print(f"PDF: {pdf}")


def org_show(settings: Settings) -> None:
    from app.organization import ASSETS, REQUISITE_KEYS, get_organization

    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            organization = get_organization(db)
            if organization is None:
                print("Данные организации не заполнены. Команда: python -m app.cli org-set --full-name …")
                return
            for label, value in (
                ("Полное наименование", organization.full_name),
                ("В блоке подписей", organization.short_name),
                ("Подписант", organization.signer),
                ("Представитель", organization.representative),
                ("Основание", organization.basis),
                ("Город по умолчанию", organization.default_city),
                ("Контакты в колонтитуле", organization.footer_text),
            ):
                print(f"{label}: {value or '—'}")
            requisites = organization.requisites or {}
            for key in REQUISITE_KEYS:
                if key in requisites:
                    print(f"{key}: {requisites[key]}")
            for kind, column in ASSETS.items():
                data = getattr(organization, column)
                print(f"{kind}: {f'{len(data)} байт' if data else 'нет'}")
            print(
                "Готово для протокола"
                if organization.ready_for_protocol
                else "Для протокола нужно полное наименование"
            )
    finally:
        engine.dispose()


def org_set(settings: Settings, args: argparse.Namespace) -> None:
    from app.organization import ensure_organization, set_requisites

    requisites: dict[str, str] = {}
    for item in args.requisite or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--requisite {item}: нужно KEY=VALUE")
        requisites[key.strip()] = value
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            organization = ensure_organization(db)
            for option, column in (
                ("full_name", "full_name"),
                ("short_name", "short_name"),
                ("signer", "signer"),
                ("representative", "representative"),
                ("basis", "basis"),
                ("city", "default_city"),
                ("footer", "footer_text"),
            ):
                value = getattr(args, option)
                if value is not None:
                    setattr(organization, column, value.strip())
            set_requisites(organization, requisites)
        print("Данные организации сохранены.")
    finally:
        engine.dispose()


def org_asset(settings: Settings, kind: str, path: Path | None, clear: bool) -> None:
    from app.organization import ensure_organization, set_asset

    if (path is None) == (not clear):
        raise SystemExit("Укажите файл или --clear.")
    data = None if clear else path.read_bytes()  # type: ignore[union-attr]
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            set_asset(ensure_organization(db), kind, data)
        print(f"{kind}: {'удалена' if clear else 'сохранена'}.")
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Служебные команды contract-review"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_wait = sub.add_parser("wait-db", help="дождаться базы")
    p_wait.add_argument("--timeout", type=float, default=60)
    sub.add_parser("migrate", help="применить миграции")
    sub.add_parser("bootstrap", help="создать первого администратора")
    p_create = sub.add_parser("create-user", help="создать пользователя")
    p_create.add_argument("--login", required=True)
    p_create.add_argument("--name", default="")
    p_create.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.MANAGER.value)
    p_password = sub.add_parser("set-password", help="сменить пароль")
    p_password.add_argument("--login", required=True)
    p_extract = sub.add_parser("extract", help="прочитать договор и показать блоки")
    p_extract.add_argument("path", type=Path)
    p_extract.add_argument("--json", action="store_true", help="вывести блоки в JSON")
    p_extract.add_argument("--width", type=int, default=100, help="сколько символов текста показывать")
    p_protocol = sub.add_parser("protocol", help="собрать протокол из JSON")
    p_protocol.add_argument("data", type=Path, help="данные протокола в JSON")
    p_protocol.add_argument("--out", type=Path, required=True, help="куда сохранить docx")
    p_protocol.add_argument("--pdf", type=Path, help="куда сохранить PDF (нужен LibreOffice)")
    p_protocol.add_argument("--header", type=Path, help="шапка бланка (PNG)")
    p_protocol.add_argument("--signature", type=Path, help="подпись (PNG с прозрачностью)")
    p_protocol.add_argument("--stamp", type=Path, help="печать (PNG с прозрачностью)")
    sub.add_parser("org-show", help="данные организации")
    p_org = sub.add_parser("org-set", help="изменить данные организации")
    p_org.add_argument("--full-name", help="как во вступлении протокола")
    p_org.add_argument("--short-name", help="как в блоке подписей")
    p_org.add_argument("--signer", help="расшифровка подписи: И.И. Иванов")
    p_org.add_argument("--representative", help="для юрлица: в лице кого")
    p_org.add_argument("--basis", help="для юрлица: на основании чего")
    p_org.add_argument("--city", help="город протокола, если в договоре его нет")
    p_org.add_argument("--footer", help="контакты для колонтитула бланка")
    p_org.add_argument("--requisite", action="append", metavar="KEY=VALUE", help="inn=…, ogrnip=…, email=…")
    p_asset = sub.add_parser("org-asset", help="подпись, печать или шапка бланка")
    p_asset.add_argument("kind", choices=["signature", "stamp", "header"])
    p_asset.add_argument("path", type=Path, nargs="?")
    p_asset.add_argument("--clear", action="store_true", help="удалить картинку")
    llm_commands.register(sub)
    args = parser.parse_args(argv)

    if args.command == "extract":
        configure_logging("WARNING")
        extract(args.path, args.json, args.width)
        return
    if args.command == "protocol":
        configure_logging("WARNING")
        protocol(args.data, args.out, args.pdf, args.header, args.signature, args.stamp)
        return

    settings = get_settings()
    if args.command in llm_commands.COMMANDS:
        # Вывод для человека в консоли: служебные сообщения библиотек не нужны.
        configure_logging("WARNING")
        llm_commands.run(args.command, args, settings)
        return
    configure_logging(settings.log_level)
    try:
        if args.command == "wait-db":
            wait_db(settings, args.timeout)
        elif args.command == "migrate":
            migrate(settings)
        elif args.command == "bootstrap":
            bootstrap(settings)
        elif args.command == "create-user":
            create_user(settings, args.login, args.name, args.role)
        elif args.command == "set-password":
            set_password(settings, args.login)
        elif args.command == "org-show":
            org_show(settings)
        elif args.command == "org-set":
            org_set(settings, args)
        elif args.command == "org-asset":
            org_asset(settings, args.kind, args.path, args.clear)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
