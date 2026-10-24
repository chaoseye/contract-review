"""Запуск внешней программы с лимитом времени (LibreOffice, Tesseract, Codex CLI).

При превышении лимита завершается вся группа процессов: LibreOffice запускает дочерний soffice.bin,
Codex — свои вспомогательные процессы, и без этого они остались бы висеть.
"""

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def run_with_timeout(
    args: Sequence[str],
    timeout: float,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Выполнить программу. env — полное окружение процесса (None — окружение текущего процесса).

    По таймауту группа процессов завершается и выбрасывается subprocess.TimeoutExpired.
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(  # noqa: S603 — аргументы формирует код, не пользователь
        list(args),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        cwd=cwd,
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)


def kill_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        # Системная утилита Windows: завершает процесс вместе с дочерними.
        command = ["taskkill", "/F", "/T", "/PID", str(process.pid)]
        subprocess.run(command, capture_output=True, check=False)  # noqa: S603
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
