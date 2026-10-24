"""Запуск LibreOffice и Tesseract с лимитом времени (общая часть — app/core/process.py)."""

import os
import subprocess
from collections.abc import Sequence

from app.core.process import run_with_timeout
from app.documents.errors import ConversionError


class ProcessTimeoutError(ConversionError):
    pass


def run_process(
    args: Sequence[str],
    timeout: float,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return run_with_timeout(args, timeout, input_bytes=input_bytes, env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired as exc:
        raise ProcessTimeoutError("Обработка документа заняла слишком много времени.") from exc
