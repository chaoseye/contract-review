"""Обработчики задач очереди.

Длинная работа (чтение, распознавание, запрос к модели) идёт вне транзакции: перед ней и после неё —
короткие сессии. Ошибки документа (DocumentError) и настроек ИИ понятны менеджеру и не повторяются.
Временная недоступность ИИ повторяется через queue.RetryLater. Прочие исключения уходят в worker:
чтение он повторяет один раз, анализ — нет (RETRY_UNEXPECTED).
Исходный файл договора удаляется, как только итог чтения записан в базу.
"""

import logging
import shutil
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app import audit, storage
from app.analysis import profiles, prompts
from app.analysis.prompt import build_request
from app.analysis.providers.base import ProviderError
from app.analysis.requisites import check_info
from app.analysis.run import run_analysis
from app.core.config import Settings
from app.core.crypto import SecretKeyError
from app.core.db import session_scope, utcnow
from app.documents.errors import DocumentError
from app.documents.extract import extract_document
from app.jobs import queue
from app.models import Check, CheckStatus, JobKind

# Задача analyze: первая попытка и до трёх повторов при временной недоступности ИИ (ARCHITECTURE, раздел 10).
ANALYZE_ATTEMPTS = 4
NO_PROFILE_REASON = "Анализ не запущен: подключение ИИ не настроено. Обратитесь к администратору."
# Повторять ли задачу после неожиданного исключения. Анализ не повторяется: ошибка могла случиться уже
# после ответа модели, и повтор снова отправил бы договор платной модели.
RETRY_UNEXPECTED: dict[str, bool] = {JobKind.EXTRACT: True, JobKind.ANALYZE: False}

log = logging.getLogger(__name__)

Handler = Callable[[sessionmaker[Session], Settings, int, str], None]


def _set_progress(factory: sessionmaker[Session], job_id: int, check_id: str, message: str) -> None:
    with session_scope(factory) as db:
        queue.heartbeat(db, job_id)
        check = db.get(Check, check_id)
        if check is not None:
            check.progress = message[:200]


def _waiting_check(db: Session, check_id: str, kind: str) -> Check | None:
    """Проверка под блокировкой строки — если она ещё ждёт итога этой задачи.

    Итог задачи записывается только так. Если задачу сочли зависшей, пока она ещё шла, второе выполнение
    могло закончиться раньше: тогда итог первого уже не нужен и не стирает правки протокола.
    Блокировка не даёт двум итогам пройти одновременно на PostgreSQL; на SQLite её нет, там поток один.
    """
    check = db.get(Check, check_id, with_for_update=True)
    if check is None or _already_done(check, kind):
        return None
    return check


def _already_done(check: Check, kind: str) -> bool:
    """Проверка ушла дальше этой задачи: повтор после незаписанного итога ничего не меняет. Иначе он стёр бы
    правки протокола и второй раз заплатил за анализ ."""
    if queue.waits_for(check, kind):
        return False
    log.info("проверка %s: задача %s уже не нужна (статус %s), пропущена", check.id, kind, check.status)
    return True


def _reading_not_needed(factory: sessionmaker[Session], check_id: str) -> bool:
    """Проверку удалили или она уже не ждёт чтения."""
    with session_scope(factory) as db:
        check = db.get(Check, check_id)
        return check is None or not queue.waits_for(check, JobKind.EXTRACT)


def handle_extract(factory: sessionmaker[Session], settings: Settings, job_id: int, check_id: str) -> None:
    with session_scope(factory) as db:
        check = db.get(Check, check_id)
        if check is None:
            return  # проверку уже удалили
        if _already_done(check, JobKind.EXTRACT):
            return
        check.status = CheckStatus.EXTRACTING
        check.progress = "Чтение документа"
        check.error = None
        source = storage.source_path(settings, check.id, check.file_kind)

    work = storage.work_dir(settings, check_id)
    try:
        document = extract_document(
            source, work, progress=lambda message: _set_progress(factory, job_id, check_id, message)
        )
    except DocumentError as exc:
        log.info("проверка %s: документ не прочитан (%s)", check_id, type(exc).__name__)
        with session_scope(factory) as db:
            check = _waiting_check(db, check_id, JobKind.EXTRACT)
            if check is not None:
                check.status = CheckStatus.FAILED
                check.progress = None
                check.error = str(exc)
        # Файл больше не нужен: повторно он не читается, менеджер загрузит исправленный.
        storage.remove_check_files(settings, check_id)
        return
    except Exception:
        # Задачу сочли зависшей, пока она шла, и другое её выполнение уже записало итог и удалило файл,
        # или проверку удалили во время чтения. Тогда ошибка этого чтения ничего не значит.
        if _reading_not_needed(factory, check_id):
            log.info("проверка %s: чтение уже не нужно, ошибка этого выполнения пропущена", check_id)
            return
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    with session_scope(factory) as db:
        check = _waiting_check(db, check_id, JobKind.EXTRACT)
        saved = check is not None
        if check is not None:
            check.blocks = [block.to_dict() for block in document.blocks]
            check.warnings = document.warnings
            check.ocr_used = document.ocr_used
            check.page_count = document.page_count
            check.status = CheckStatus.EXTRACTED
            check.progress = None
            check.error = None
            start_analysis(db, check)
    # Текст уже в базе, исходный файл больше не нужен. Удаляется только после записи итога: если
    # записать не удалось, повтор задачи прочитает файл снова.
    storage.remove_check_files(settings, check_id)
    if saved:
        log.info(
            "проверка %s: прочитано блоков %s, OCR %s", check_id, len(document.blocks), document.ocr_used
        )


def start_analysis(db: Session, check: Check) -> bool:
    """Поставить анализ в очередь, если есть активное подключение ИИ. Иначе — записать причину."""
    if profiles.active_profile(db) is None:
        check.analysis = {"skipped": NO_PROFILE_REASON}
        return False
    check.result = None
    check.analysis = None
    # Правки протокола относились к прежнему результату анализа.
    check.protocol = None
    # Статус analyzing — с постановки в очередь: если задача упадёт до начала анализа, ошибка дойдёт до
    # проверки, а не оставит её «ожидающей» навсегда.
    check.status = CheckStatus.ANALYZING
    check.progress = "Ожидает анализа"
    check.error = None
    queue.enqueue(db, JobKind.ANALYZE, check.id, max_attempts=ANALYZE_ATTEMPTS)
    return True


def _fail_analysis(
    factory: sessionmaker[Session], check_id: str, message: str, meta: dict[str, Any] | None = None
) -> None:
    with session_scope(factory) as db:
        check = _waiting_check(db, check_id, JobKind.ANALYZE)
        if check is not None:
            _fail_now(db, check, message, {**(check.analysis or {}), **(meta or {})})


def _fail_now(db: Session, check: Check, message: str, meta: dict[str, Any] | None = None) -> None:
    """Анализ не выполнен: ошибка видна менеджеру и записана в журнал."""
    check.status = CheckStatus.FAILED
    check.progress = None
    check.error = message
    if meta is not None:
        check.analysis = meta
    model = meta.get("model") if meta else None
    audit.record(db, "analysis_failed", user_id=check.owner_id, check=check, model=model, details=message)


def handle_analyze(factory: sessionmaker[Session], settings: Settings, job_id: int, check_id: str) -> None:
    with session_scope(factory) as db:
        check = db.get(Check, check_id)
        if check is None:
            return  # проверку уже удалили
        if _already_done(check, JobKind.ANALYZE):
            return
        if not check.blocks:
            _fail_now(db, check, "В документе нет текста для анализа.")
            return
        profile = profiles.active_profile(db)
        if profile is None:
            _fail_now(db, check, NO_PROFILE_REASON)
            return
        prompt_version = prompts.active_version(db)
        admin_prompt = prompt_version.text
        meta: dict[str, Any] = {
            "profile_id": profile.id,
            "profile": profile.name,
            "kind": profile.kind,
            "model": profile.model,
            "prompt_version_id": prompt_version.id,
            "prompt_fingerprint": prompt_version.fingerprint,
            "started_at": utcnow().isoformat(),
        }
        try:
            provider = profiles.build_provider(profile, settings)
        except (profiles.ProfileError, SecretKeyError, ProviderError) as exc:
            message = exc.message if isinstance(exc, ProviderError) else str(exc)
            _fail_now(db, check, f"Анализ не выполнен: {message}", meta)
            return
        blocks = list(check.blocks)
        info = check_info(db, check)
        meta["our_role"] = info.our_role
        check.status = CheckStatus.ANALYZING
        check.progress = f"Анализ договора: {profile.name}"
        check.error = None
        check.result = None
        check.analysis = meta

    request = build_request(blocks, info, admin_prompt)
    try:
        with queue.keep_alive(factory, job_id):
            outcome = run_analysis(provider, request, blocks)
    except ProviderError as exc:
        error = {"error": {"type": type(exc).__name__, "message": exc.message, "detail": exc.detail}}
        log.info("проверка %s: анализ не выполнен (%s)", check_id, type(exc).__name__)
        if exc.retryable:
            with session_scope(factory) as db:
                check = _waiting_check(db, check_id, JobKind.ANALYZE)
                if check is not None:
                    check.analysis = {**(check.analysis or {}), **error}
            raise queue.RetryLater(
                message=f"Анализ не выполнен: {exc.message}",
                progress=f"{exc.message} Анализ повторится автоматически.",
                cause=type(exc).__name__,
            ) from exc
        _fail_analysis(factory, check_id, f"Анализ не выполнен: {exc.message}", error)
        return

    usage = outcome.usage
    with session_scope(factory) as db:
        check = _waiting_check(db, check_id, JobKind.ANALYZE)
        if check is None:
            return
        check.result = outcome.checked.to_dict()
        check.analysis = {
            **(check.analysis or {}),
            "model": outcome.model,
            "response_format": outcome.response_format,
            "attempts": outcome.attempts,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_rub": usage.cost_rub,
            "cost_estimated": usage.cost_estimated,
            "duration_seconds": round(outcome.duration_seconds, 1),
            "finished_at": utcnow().isoformat(),
            "error": None,
        }
        check.status = CheckStatus.READY
        check.progress = None
        check.error = None
        audit.record(
            db,
            "analysis_done",
            user_id=check.owner_id,
            check=check,
            model=outcome.model,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cost_rub=usage.cost_rub,
            prompt_version=check.analysis.get("prompt_fingerprint"),
        )
    # В лог — только числа: тексты рисков и цитаты туда не пишутся.
    log.info(
        "проверка %s: анализ готов, рисков %s, модель %s, токены %s/%s, стоимость %s, попыток %s",
        check_id,
        len(outcome.checked.answer.get("risks", [])),
        outcome.model,
        usage.input_tokens,
        usage.output_tokens,
        usage.cost_rub,
        outcome.attempts,
    )


HANDLERS: dict[str, Handler] = {JobKind.EXTRACT: handle_extract, JobKind.ANALYZE: handle_analyze}
