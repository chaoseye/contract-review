"""Файлы проверок на диске: DATA_DIR/checks/<id>/source.<тип>.

Файл удаляется, как только итог чтения записан в базу. Если чтение оборвалось окончательно, его
удаляет очистка, а самое позднее — удаление проверки.
"""

import re
import shutil
from pathlib import Path

from app.core.config import Settings

CHECK_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def checks_root(settings: Settings) -> Path:
    return settings.data_dir / "checks"


def uploads_root(settings: Settings) -> Path:
    return settings.data_dir / "uploads"


def temp_root(settings: Settings) -> Path:
    """Временные папки внешних программ (заданий Codex). Удаляются после задания и очисткой."""
    return settings.data_dir / "tmp"


def check_dir(settings: Settings, check_id: str) -> Path:
    if not CHECK_ID_RE.match(check_id):
        raise ValueError("Некорректный идентификатор проверки")
    return checks_root(settings) / check_id


def source_path(settings: Settings, check_id: str, file_kind: str) -> Path:
    return check_dir(settings, check_id) / f"source.{file_kind}"


def work_dir(settings: Settings, check_id: str) -> Path:
    return check_dir(settings, check_id) / "work"


def remove_check_files(settings: Settings, check_id: str) -> None:
    shutil.rmtree(check_dir(settings, check_id), ignore_errors=True)
