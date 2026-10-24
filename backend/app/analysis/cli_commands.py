"""Команды `python -m app.cli llm-*`, `codex-*`, `analyze` и `evaluate`: ИИ из командной строки.

Подключения и вход в Codex есть и в админке (раздел «Подключения ИИ»); команды остаются для разработки,
оценки на примерах и случая, когда интерфейс недоступен.

Здесь только описание аргументов: оно не загружает SDK openai и anthropic, чтобы остальные команды CLI
(wait-db, migrate при старте контейнера) запускались быстро. Реализация — app/analysis/cli_impl.py.

Ключ API не передаётся аргументом (он остался бы в истории команд и в списке процессов): команда
спрашивает его в консоли или читает первую строку stdin.
"""

import argparse
from pathlib import Path

from app.analysis.presets import PRESETS
from app.core.config import Settings
from app.models import LlmKind, Procurement

COMMANDS = {
    "llm-list",
    "llm-add",
    "llm-set",
    "llm-activate",
    "llm-delete",
    "llm-models",
    "llm-test",
    "codex-login",
    "codex-status",
    "codex-logout",
    "analyze",
    "evaluate",
}


def _add_param_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, help="лимит времени запроса, секунд (по умолчанию 300)")
    parser.add_argument("--retries", type=int, help="повторы при сетевых ошибках, 429 и 5xx (по умолчанию 2)")
    parser.add_argument(
        "--response-format",
        choices=["auto", "json_schema", "json_object", "none"],
        help="как просить JSON: auto выбирает по возможностям модели",
    )
    parser.add_argument("--temperature", type=float, help="температура; без параметра — значение модели")
    parser.add_argument("--max-output-tokens", type=int, help="лимит длины ответа в токенах")
    parser.add_argument(
        "--russia-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="RouterAI: только провайдеры с серверами в РФ",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Anthropic: глубина рассуждений модели (у готового адреса anthropic — high)",
    )
    parser.add_argument("--extra-body", help='дополнительные поля запроса в JSON, например {"reasoning": {}}')
    parser.add_argument(
        "--codex-config",
        action="append",
        metavar="KEY=VALUE",
        help="Codex CLI: параметр --config, можно несколько",
    )


def register(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("llm-list", help="подключения ИИ")

    p_add = sub.add_parser("llm-add", help="добавить подключение ИИ")
    p_add.add_argument("name")
    p_add.add_argument(
        "--kind",
        choices=[kind.value for kind in LlmKind],
        help="тип подключения; по умолчанию — тип готового адреса или openai_compat",
    )
    p_add.add_argument("--preset", choices=sorted(PRESETS), help="готовый адрес API")
    p_add.add_argument("--base-url", help="адрес API")
    p_add.add_argument(
        "--model", default="", help="модель, например anthropic/claude-sonnet-5.5 или claude-opus-5-5"
    )
    p_add.add_argument("--no-key", action="store_true", help="не спрашивать ключ API")
    p_add.add_argument("--activate", action="store_true", help="сделать активным")
    _add_param_options(p_add)

    p_set = sub.add_parser("llm-set", help="изменить подключение ИИ")
    p_set.add_argument("name")
    p_set.add_argument("--base-url")
    p_set.add_argument("--model")
    p_set.add_argument("--key", action="store_true", help="ввести новый ключ API")
    p_set.add_argument("--clear-key", action="store_true", help="удалить ключ API")
    _add_param_options(p_set)

    for command, text in (
        ("llm-activate", "сделать подключение активным"),
        ("llm-delete", "удалить подключение"),
    ):
        sub.add_parser(command, help=text).add_argument("name")

    p_models = sub.add_parser("llm-models", help="модели провайдера с ценами")
    p_models.add_argument("name")
    p_models.add_argument("--search", default="", help="часть названия модели")
    p_models.add_argument("--all", action="store_true", help="показать все, а не первые 50")

    sub.add_parser("llm-test", help="проверить связь").add_argument("name")

    p_login = sub.add_parser("codex-login", help="войти в Codex по коду устройства")
    p_login.add_argument("--timeout", type=float, default=900, help="сколько ждать вход, секунд")
    sub.add_parser("codex-status", help="состояние входа в Codex")
    sub.add_parser("codex-logout", help="выйти из Codex")

    p_analyze = sub.add_parser("analyze", help="прочитать договор и проанализировать его")
    p_analyze.add_argument("path", type=Path, help="договор: docx, doc, rtf или PDF")
    _add_analysis_options(p_analyze)

    p_eval = sub.add_parser("evaluate", help="сравнить анализ с готовым протоколом разногласий")
    p_eval.add_argument(
        "expected", type=Path, help="ожидаемые строки протокола в JSON (формат — в app/analysis/evaluate.py)"
    )
    source = p_eval.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="договор: прочитать и проанализировать")
    source.add_argument(
        "--answer", type=Path, help="сохранённый результат analyze --out: без запроса к модели"
    )
    _add_analysis_options(p_eval)
    p_eval.add_argument("--metrics", type=Path, help="сохранить показатели в JSON (без текстов)")


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="подключение ИИ; без параметра — активное")
    parser.add_argument(
        "--procurement", choices=[item.value for item in Procurement], default="unknown", help="режим закупки"
    )
    parser.add_argument("--comment", default="", help="параметры сделки и что учесть")
    parser.add_argument("--out", type=Path, help="сохранить результат анализа в JSON")


def run(command: str, args: argparse.Namespace, settings: Settings) -> None:
    from app.analysis import cli_impl

    cli_impl.run(command, args, settings)
