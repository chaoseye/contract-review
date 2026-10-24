"""Реализация команд `python -m app.cli llm-*` и `codex-*` (регистрация — app/analysis/cli_commands.py).

Модуль загружает SDK openai и anthropic, поэтому импортируется только при запуске этих команд.
"""

import argparse
import getpass
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analysis import presets, profiles, prompts
from app.analysis.evaluate import evaluate, load_expected
from app.analysis.prompt import CheckInfo, build_request
from app.analysis.providers.anthropic_api import AnthropicProvider
from app.analysis.providers.base import ModelInfo, ProviderError
from app.analysis.providers.codex_cli import DeviceLogin, codex_logout, codex_status, resolve_codex_command
from app.analysis.providers.openai_compat import OpenAICompatProvider
from app.analysis.run import run_analysis
from app.core.config import Settings
from app.core.crypto import SecretKeyError
from app.core.db import make_engine, make_sessionmaker, session_scope
from app.models import LlmKind, LlmProfile

CURRENCY_SIGN = {"RUB": "₽", "USD": "$"}


def read_secret(prompt: str) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass(prompt)


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for option, key in (
        ("timeout", "timeout_seconds"),
        ("retries", "max_retries"),
        ("response_format", "response_format"),
        ("temperature", "temperature"),
        ("max_output_tokens", "max_output_tokens"),
        ("russia_only", "russia_only"),
        ("effort", "effort"),
    ):
        value = getattr(args, option)
        if value is not None:
            params[key] = value
    if args.extra_body:
        try:
            params["extra_body"] = json.loads(args.extra_body)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--extra-body: это не JSON ({exc.msg})") from exc
    if args.codex_config:
        params["codex_config"] = args.codex_config
    return params


def _decimal(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _money(value: float | None, currency: str | None) -> str:
    if value is None or currency is None:
        return "—"
    return f"{_decimal(value * 1_000_000, 2)} {CURRENCY_SIGN.get(currency, currency)}"


def _format_support(info: ModelInfo) -> str:
    if info.supports("structured_outputs"):
        return "схема"
    if info.supports("response_format"):
        return "JSON"
    return "?" if info.supported_parameters is None else "—"


def _describe(profile: LlmProfile) -> str:
    params = profiles.profile_params(profile)
    key = (
        f"ключ …{params.key_hint}"
        if profile.api_key_enc and params.key_hint
        else ("ключ задан" if profile.api_key_enc else "без ключа")
    )
    flags = ["РФ"] if params.russia_only else []
    if params.effort:
        flags.append(f"effort {params.effort}")
    parts = [
        "*" if profile.is_active else " ",
        profile.name,
        profile.kind,
        profile.base_url or "—",
        profile.model or "модель по умолчанию",
        key,
        f"{params.timeout_seconds} с",
        *flags,
    ]
    return "  ".join(parts)


def _print_report(profile: LlmProfile, report: Any) -> None:
    print(f"Подключение «{profile.name}»: связь есть.")
    info: ModelInfo | None = report.model_info
    if info is not None:
        context = f", контекст {info.context_length} токенов" if info.context_length else ""
        if info.max_output_tokens:
            context += f", ответ до {info.max_output_tokens}"
        print(f"Модель: {info.id}{' — ' + info.name if info.name else ''}{context}")
        if info.currency:
            where = " (провайдеры с серверами в РФ)" if info.countries == ["ru"] else ""
            print(
                f"Цена за 1 млн токенов: вход {_money(info.price_input, info.currency)}, "
                f"выход {_money(info.price_output, info.currency)}{where}"
            )
    result = report.result
    usage = result.usage
    cost = ""
    if usage.cost_rub is not None:
        cost = f"; стоимость {'≈ ' if usage.cost_estimated else ''}{_decimal(usage.cost_rub, 4)} ₽"
    print(f"Формат ответа: {result.response_format}")
    print(
        f"Ответ за {_decimal(result.duration_seconds, 1)} с; токены: вход {usage.input_tokens}, "
        f"выход {usage.output_tokens}{cost}"
    )
    for note in report.notes:
        print(f"! {note}")


def _fail(exc: ProviderError) -> SystemExit:
    message = f"Проверка не прошла: {exc.message}"
    if exc.detail:
        message += f"\nОтвет провайдера: {exc.detail}"
    return SystemExit(message)


def _with_db(settings: Settings, action: Any) -> Any:
    engine = make_engine(settings.database_url)
    try:
        with session_scope(make_sessionmaker(engine)) as db:
            return action(db)
    finally:
        engine.dispose()


def _add(settings: Settings, args: argparse.Namespace) -> None:
    preset = presets.PRESETS.get(args.preset) if args.preset else None
    kind = args.kind or (preset.kind if preset else LlmKind.OPENAI_COMPAT.value)
    if preset is not None and preset.kind != kind:
        raise SystemExit(f"Готовый адрес «{args.preset}» — для подключений {preset.kind}, а не {kind}.")
    params = _params_from_args(args)
    base_url = args.base_url or (preset.base_url if preset else "")
    if preset is not None and "russia_only" not in params:
        params["russia_only"] = preset.russia_only
    if preset is not None and preset.effort and "effort" not in params:
        params["effort"] = preset.effort
    api_key: str | None = None
    needs_key = kind in (LlmKind.OPENAI_COMPAT, LlmKind.ANTHROPIC) and (preset is None or preset.needs_key)
    if needs_key and not args.no_key:
        api_key = read_secret("Ключ API: ")
        if not api_key.strip():
            raise SystemExit("Ключ не введён. Для подключения без ключа (Ollama) укажите --no-key.")

    def action(db: Session) -> None:
        profile = profiles.create_profile(
            db,
            settings,
            name=args.name,
            kind=kind,
            base_url=base_url,
            model=args.model,
            api_key=api_key,
            params=params,
            activate=args.activate,
        )
        print(f"Добавлено подключение «{profile.name}»{' (активное)' if profile.is_active else ''}.")

    _with_db(settings, action)


def _set(settings: Settings, args: argparse.Namespace) -> None:
    if args.key and args.clear_key:
        raise SystemExit("Укажите либо --key, либо --clear-key.")
    api_key = read_secret("Новый ключ API: ") if args.key else ("" if args.clear_key else None)
    if args.key and not (api_key or "").strip():
        raise SystemExit("Ключ не введён.")
    params = _params_from_args(args)

    def action(db: Session) -> None:
        profile = profiles.get_profile(db, args.name)
        profiles.update_profile(
            db, settings, profile, base_url=args.base_url, model=args.model, api_key=api_key, params=params
        )
        print(f"Подключение «{profile.name}» изменено.")

    _with_db(settings, action)


def _models(settings: Settings, args: argparse.Namespace) -> None:
    def action(db: Session) -> list[ModelInfo]:
        profile = profiles.get_profile(db, args.name)
        provider = profiles.build_provider(profile, settings)
        if not isinstance(provider, OpenAICompatProvider | AnthropicProvider):
            raise SystemExit("У Codex CLI нет списка моделей.")
        return provider.list_models()

    try:
        models = _with_db(settings, action)
    except ProviderError as exc:
        raise _fail(exc) from exc
    search = args.search.lower()
    found = [info for info in models if search in info.id.lower() or search in info.name.lower()]
    shown = found if args.all else found[:50]
    print("модель | контекст | вход за 1 млн | выход за 1 млн | формат ответа")
    for info in shown:
        context = f"{info.context_length // 1000}K" if info.context_length else "—"
        print(
            f"{info.id} | {context} | {_money(info.price_input, info.currency)} | "
            f"{_money(info.price_output, info.currency)} | {_format_support(info)}"
        )
    if len(shown) < len(found):
        print(f"… и ещё {len(found) - len(shown)}. Уточните --search или добавьте --all.")
    print("Формат ответа: «схема» — structured outputs, «JSON» — JSON-режим, «?» — неизвестно.")
    if models and all(info.currency is None for info in models):
        print("Провайдер не сообщает цен в списке моделей: стоимость анализа не считается.")
    else:
        print("Цены — минимальные по модели. С «Только серверы в РФ» точную цену покажет llm-test.")


def _test(settings: Settings, args: argparse.Namespace) -> None:
    def action(db: Session) -> None:
        profile = profiles.get_profile(db, args.name)
        try:
            report = profiles.check_profile(db, settings, profile)
        except ProviderError as exc:
            raise _fail(exc) from exc
        _print_report(profile, report)

    _with_db(settings, action)


def _codex_login(settings: Settings, timeout: float) -> None:
    login = DeviceLogin(resolve_codex_command(settings.codex_bin), profiles.codex_home(settings))
    login.start()
    if login.wait_for_code(60):
        print(f"Откройте {login.url}, войдите в аккаунт ChatGPT и введите код {login.code}.")
        print(f"Жду вход до {int(timeout // 60)} минут…")
    else:
        print("Codex не показал ссылку и код. Его вывод:")
        for line in login.lines:
            print(f"  {line}")
    if login.wait(timeout):
        print("Вход в Codex выполнен.")
        return
    raise SystemExit("Вход в Codex не выполнен.")


SEVERITY_LABELS = {"high": "высокая", "medium": "средняя", "low": "низкая"}
QUOTE_LABELS = {
    "exact": "цитата найдена",
    "fuzzy": "цитата найдена неточно",
    "not_found": "цитата НЕ найдена",
    "empty": "без цитаты",
    "new_clause": "новый пункт",
}


def _analyze_file(settings: Settings, args: argparse.Namespace, path: Path) -> dict[str, Any]:
    """Прочитать договор и проанализировать. Возвращает {"result": …, "analysis": …}, как в проверке."""
    from app.documents.errors import DocumentError
    from app.documents.extract import extract_document

    def provider_for(db: Session) -> tuple[Any, LlmProfile, str, str]:
        profile = profiles.get_profile(db, args.profile) if args.profile else profiles.active_profile(db)
        if profile is None:
            raise SystemExit("Нет активного подключения ИИ. Добавьте: python -m app.cli llm-add …")
        version = prompts.active_version(db)
        return profiles.build_provider(profile, settings), profile, version.text, version.fingerprint

    provider, profile, admin_prompt, fingerprint = _with_db(settings, provider_for)
    with tempfile.TemporaryDirectory(prefix="contract-review-analyze-") as work:
        try:
            document = extract_document(path, Path(work), progress=lambda message: print(f"… {message}"))
        except DocumentError as exc:
            raise SystemExit(f"Не удалось прочитать: {exc}") from exc
    blocks = [block.to_dict() for block in document.blocks]
    info = CheckInfo(
        procurement=args.procurement,
        comment=args.comment,
        ocr_used=document.ocr_used,
        page_count=document.page_count,
        warnings=document.warnings,
    )
    print(f"… блоков: {len(blocks)}, запрос к модели через «{profile.name}»")
    outcome = run_analysis(provider, build_request(blocks, info, admin_prompt), blocks)
    usage = outcome.usage
    return {
        "result": outcome.checked.to_dict(),
        "analysis": {
            "profile": profile.name,
            "kind": profile.kind,
            "model": outcome.model,
            "response_format": outcome.response_format,
            "attempts": outcome.attempts,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_rub": usage.cost_rub,
            "cost_estimated": usage.cost_estimated,
            "duration_seconds": round(outcome.duration_seconds, 1),
            "prompt_fingerprint": fingerprint,
        },
    }


def _print_analysis(saved: dict[str, Any]) -> None:
    """Сводка анализа в консоль. Только для ручной проверки: в логи этот текст не попадает."""
    result, meta = saved["result"], saved.get("analysis") or {}
    answer, checks = result["answer"], result["risk_checks"]
    if meta:
        cost = ""
        if meta.get("cost_rub") is not None:
            cost = (
                f", стоимость {'≈ ' if meta.get('cost_estimated') else ''}{_decimal(meta['cost_rub'], 2)} ₽"
            )
        print(
            f"Модель: {meta.get('model')} («{meta.get('profile')}», {meta.get('response_format')}), "
            f"попыток {meta.get('attempts')}, {_decimal(meta.get('duration_seconds') or 0, 1)} с, "
            f"токены {meta.get('input_tokens')}/{meta.get('output_tokens')}{cost}"
        )
    print(f"\nВывод: {answer['summary']}")
    approval = answer["approval"]
    verdict = "можно" if approval["allowed"] else "ЗАПРЕЩЕНО"
    print(
        f"Согласование без правок: {verdict}"
        + (f" — {'; '.join(approval['reasons'])}" if approval["reasons"] else "")
    )
    confidence = answer["confidence"]
    print(
        f"Уверенность: {SEVERITY_LABELS.get(confidence['level'], confidence['level'])}"
        + (f" — {'; '.join(confidence['reasons'])}" if confidence["reasons"] else "")
    )
    risks = {risk["id"]: risk for risk in answer["risks"]}

    def line(risk: dict[str, Any]) -> str:
        check = checks.get(risk["id"], {})
        stop = ", стоп-фактор" if risk["stop_factor"] else ""
        pages = f", стр. {', '.join(map(str, check['pages']))}" if check.get("pages") else ""
        clauses = ", ".join(risk["clauses"]) or "—"
        status = QUOTE_LABELS.get(check.get("quote_status", ""), "")
        return (
            f"  {risk['id']} [{SEVERITY_LABELS.get(risk['severity'], risk['severity'])}{stop}] п. {clauses}: "
            f"{risk['title']} ({status}{pages})"
        )

    print("\nГлавные риски:")
    for risk_id in answer["top_risks"]:
        print(line(risks[risk_id]))
    print(f"\nВсе риски ({len(risks)}):")
    for risk in risks.values():
        print(line(risk))
    with_wording = sum(1 for risk in risks.values() if risk["proposed_wording"])
    print(
        f"\nС новой редакцией: {with_wording}; обязательств: {len(answer['obligations'])}; "
        f"противоречий: {len(answer['contradictions'])}; недостающих материалов: {len(answer['missing'])}; "
        f"выгодных условий: {len(answer['favorable'])}; "
        f"проверить специалистам: {len(answer['specialist_checks'])}"
    )
    for warning in result["warnings"]:
        print(f"! {warning}")


def _analyze(settings: Settings, args: argparse.Namespace) -> None:
    saved = _analyze_file(settings, args, args.path)
    _print_analysis(saved)
    if args.out:
        args.out.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nРезультат: {args.out}")


def _evaluate(settings: Settings, args: argparse.Namespace) -> None:
    expected = load_expected(args.expected)
    if args.answer:
        saved = json.loads(args.answer.read_text(encoding="utf-8"))
    else:
        saved = _analyze_file(settings, args, args.file)
        _print_analysis(saved)
        if args.out:
            args.out.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    result = evaluate(expected, saved["result"]["answer"])
    topics = {row.n: row.topic for row in expected.rows}
    yes = {True: "да", False: "нет"}
    print(f"\nОценка: {expected.name}")
    print(
        f"Найдено строк протокола: {len(result.found_rows)} из {result.total_rows} "
        f"({round(result.recall * 100)}%) — порог 80%: {yes[result.criteria['recall_80']]}"
    )
    missed_mandatory = ", ".join(map(str, result.mandatory_missed)) or "нет"
    found_mandatory = result.mandatory_total - len(result.mandatory_missed)
    print(
        f"Обязательные строки: {found_mandatory} из {result.mandatory_total}, "
        f"не найдены: {missed_mandatory} — все найдены: {yes[result.criteria['all_mandatory']]}"
    )
    extra = ", ".join(result.extra_risks) or "нет"
    print(
        f"Лишние риски: {len(result.extra_risks)} из {result.risks_total} ({extra}) — "
        f"не больше одного на три найденных: {yes[result.criteria['extra_at_most_one_per_three']]}"
    )
    for n in result.found_outside_risks:
        refs = ", ".join(result.matches[n])
        print(f"  строка {n} найдена не в рисках ({refs}), строки протокола для неё нет: {topics[n]}")
    for n in result.missed_rows:
        print(f"  не найдена строка {n}: {topics[n]}")
    print("Совпадение по смыслу и качество редакций проверяет человек.")
    if args.metrics:
        metrics = {"name": expected.name, **result.to_dict(), "analysis": saved.get("analysis")}
        args.metrics.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Показатели: {args.metrics}")


def run(command: str, args: argparse.Namespace, settings: Settings) -> None:
    try:
        if command == "llm-list":
            items = _with_db(
                settings, lambda db: [_describe(profile) for profile in profiles.list_profiles(db)]
            )
            print("\n".join(items) if items else "Подключений нет. Добавьте: python -m app.cli llm-add …")
        elif command == "llm-add":
            _add(settings, args)
        elif command == "llm-set":
            _set(settings, args)
        elif command == "llm-activate":
            _with_db(settings, lambda db: profiles.activate_profile(db, profiles.get_profile(db, args.name)))
            print(f"Активное подключение — «{args.name}».")
        elif command == "llm-delete":
            _with_db(settings, lambda db: profiles.delete_profile(db, profiles.get_profile(db, args.name)))
            print(f"Подключение «{args.name}» удалено.")
        elif command == "llm-models":
            _models(settings, args)
        elif command == "llm-test":
            _test(settings, args)
        elif command == "codex-login":
            _codex_login(settings, args.timeout)
        elif command == "codex-status":
            status = codex_status(resolve_codex_command(settings.codex_bin), profiles.codex_home(settings))
            print(
                ("Вход выполнен" if status.logged_in else "Входа нет")
                + (f": {status.message}" if status.message else "")
            )
            if not status.logged_in:
                raise SystemExit(1)
        elif command == "analyze":
            _analyze(settings, args)
        elif command == "evaluate":
            _evaluate(settings, args)
        elif command == "codex-logout":
            codex_logout(resolve_codex_command(settings.codex_bin), profiles.codex_home(settings))
            print("Выход из Codex выполнен.")
    except (profiles.ProfileError, SecretKeyError) as exc:
        raise SystemExit(str(exc)) from exc
    except ProviderError as exc:
        raise _fail(exc) from exc
