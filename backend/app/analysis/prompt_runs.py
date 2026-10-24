"""Прогон черновика промпта на договоре без сохранения.

Прогон идёт в отдельном потоке процесса приложения, результат лежит только в памяти и не дольше часа:
в базу и в проверку он не попадает, ответ модели не пишется в лог. Одновременно идёт
не больше одного прогона: запрос к модели стоит денег и может длиться минуты.
"""

import logging
import threading
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from app.analysis.providers.base import AnalysisRequest, Provider, ProviderError
from app.analysis.run import AnalysisOutcome, run_analysis
from app.core.db import utcnow

log = logging.getLogger(__name__)

RUN_TTL = timedelta(hours=1)


class RunBusy(RuntimeError):
    """Уже идёт другой прогон."""


@dataclass
class PromptRun:
    id: str
    user_id: int
    check_id: str
    profile: str
    fingerprint: str
    started_at: datetime
    status: Literal["running", "done", "failed"] = "running"
    finished_at: datetime | None = None
    # Ответ модели и его проверка (CheckedAnswer.to_dict) — только в памяти.
    result: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    detail: str | None = None


OnFinish = Callable[[PromptRun, AnalysisOutcome | None], None]


class PromptRuns:
    def __init__(self, ttl: timedelta = RUN_TTL) -> None:
        self._ttl = ttl
        self._runs: dict[str, PromptRun] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def _purge(self) -> None:
        now = utcnow()
        for run_id, run in list(self._runs.items()):
            if run.status != "running" and run.finished_at and now - run.finished_at > self._ttl:
                del self._runs[run_id]
                self._threads.pop(run_id, None)

    def start(
        self,
        *,
        user_id: int,
        check_id: str,
        profile: str,
        fingerprint: str,
        provider: Provider,
        request: AnalysisRequest,
        blocks: Iterable[Mapping[str, Any]],
        on_finish: OnFinish | None = None,
    ) -> PromptRun:
        with self._lock:
            self._purge()
            if any(run.status == "running" for run in self._runs.values()):
                raise RunBusy("Уже идёт прогон черновика. Дождитесь его окончания.")
            run = PromptRun(
                id=uuid.uuid4().hex,
                user_id=user_id,
                check_id=check_id,
                profile=profile,
                fingerprint=fingerprint,
                started_at=utcnow(),
            )
            self._runs[run.id] = run
            thread = threading.Thread(
                target=self._work,
                args=(run, provider, request, list(blocks), on_finish),
                name=f"prompt-run-{run.id[:8]}",
                daemon=True,
            )
            self._threads[run.id] = thread
        thread.start()
        return run

    def _work(
        self,
        run: PromptRun,
        provider: Provider,
        request: AnalysisRequest,
        blocks: list[Mapping[str, Any]],
        on_finish: OnFinish | None,
    ) -> None:
        outcome: AnalysisOutcome | None = None
        try:
            outcome = run_analysis(provider, request, blocks)
        except ProviderError as exc:
            run.error, run.detail = f"Прогон не выполнен: {exc.message}", exc.detail
            log.info("прогон черновика промпта не выполнен (%s)", type(exc).__name__)
        except Exception as exc:
            run.error = "Прогон не выполнен: внутренняя ошибка."
            # Текст исключения в лог не пишется: в нём может быть ответ модели.
            log.error(
                "прогон черновика промпта: %s\n%s",
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
        else:
            usage = outcome.usage
            run.result = outcome.checked.to_dict()
            run.usage = {
                "model": outcome.model,
                "response_format": outcome.response_format,
                "attempts": outcome.attempts,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_rub": usage.cost_rub,
                "cost_estimated": usage.cost_estimated,
                "duration_seconds": round(outcome.duration_seconds, 1),
            }
        run.finished_at = utcnow()
        run.status = "done" if run.result is not None else "failed"
        if on_finish is not None:
            try:
                on_finish(run, outcome)
            except Exception as exc:
                log.error("прогон черновика промпта: запись в журнал не удалась (%s)", type(exc).__name__)

    def get(self, run_id: str) -> PromptRun | None:
        with self._lock:
            self._purge()
            return self._runs.get(run_id)

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
            self._threads.pop(run_id, None)

    def join(self, run_id: str, timeout: float | None = None) -> None:
        """Дождаться окончания прогона (для тестов)."""
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)
