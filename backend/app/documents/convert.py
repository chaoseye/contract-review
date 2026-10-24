"""Перевод .doc и .rtf в .docx (и позже docx в PDF) через LibreOffice без интерфейса."""

import shutil
import sys
import tempfile
from pathlib import Path

from app.documents.errors import ConversionError, ToolMissingError
from app.documents.process import run_process

SOFFICE_TIMEOUT_SECONDS = 120

FILTERS = {
    "docx": "docx:MS Word 2007 XML",
    "pdf": "pdf:writer_pdf_Export",
    # Только для тестов: получить .doc из docx.
    "doc": "doc:MS Word 97",
}

WINDOWS_CANDIDATES = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
)


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for candidate in WINDOWS_CANDIDATES:
            if candidate.is_file():
                return str(candidate)
    return None


def convert(source: Path, target: str, out_dir: Path, timeout: float = SOFFICE_TIMEOUT_SECONDS) -> Path:
    soffice = find_soffice()
    if soffice is None:
        raise ToolMissingError(
            f"На сервере не установлен LibreOffice, файл нельзя перевести в {target.upper()}."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = out_dir / f"{source.stem}.{target}"
    expected.unlink(missing_ok=True)
    # Отдельный профиль на каждый запуск: параллельные конвертации не мешают друг другу,
    # а в новом профиле макросы по умолчанию не выполняются.
    with tempfile.TemporaryDirectory(prefix="contract-review-lo-") as profile:
        result = run_process(
            [
                soffice,
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--headless",
                "--invisible",
                "--norestore",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--convert-to",
                FILTERS[target],
                "--outdir",
                str(out_dir),
                str(source),
            ],
            timeout=timeout,
            env={"SAL_USE_VCLPLUGIN": "svp"},
        )
    if result.returncode != 0 or not expected.is_file() or expected.stat().st_size == 0:
        raise ConversionError("Не удалось открыть файл: возможно, он повреждён или защищён паролем.")
    return expected
