"""Определение типа файла по содержимому, а не по расширению (documentation/ARCHITECTURE.md, раздел 2)."""

import zipfile
import zlib
from pathlib import Path

from app.documents.errors import DamagedFileError, ProtectedFileError, UnsupportedFileError
from app.documents.model import FileKind

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_SIGNATURE = b"PK\x03\x04"
UNSUPPORTED_MESSAGE = "Формат файла не поддерживается. Загрузите договор в docx, doc или PDF."

# Защита от zip-бомб: настоящие договоры на порядки меньше.
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024

WORD_MAIN_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
)


def _utf16(name: str) -> bytes:
    return name.encode("utf-16-le")


def detect_kind(path: Path) -> FileKind:
    with path.open("rb") as handle:
        head = handle.read(1024)
    if not head:
        raise DamagedFileError("Файл пустой.")
    if b"%PDF-" in head:
        return FileKind.PDF
    if head.startswith(ZIP_SIGNATURE):
        _check_docx(path)
        return FileKind.DOCX
    if head.startswith(OLE_SIGNATURE):
        return _check_ole(path)
    if head.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"{\\rtf"):
        # Многие «.doc» на деле RTF; LibreOffice переводит его в docx так же, как doc.
        return FileKind.RTF
    raise UnsupportedFileError(UNSUPPORTED_MESSAGE)


def _check_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
                raise DamagedFileError("Файл повреждён или слишком велик для документа Word.")
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names:
                raise UnsupportedFileError(UNSUPPORTED_MESSAGE)
            content_types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise DamagedFileError("Файл повреждён: не удаётся открыть архив документа.") from exc
    if not any(content_type in content_types for content_type in WORD_MAIN_CONTENT_TYPES):
        raise UnsupportedFileError(UNSUPPORTED_MESSAGE)


def _check_ole(path: Path) -> FileKind:
    """Файл формата OLE: старый Word (.doc), Excel, PowerPoint или зашифрованный docx."""
    data = path.read_bytes()
    if _utf16("EncryptedPackage") in data:
        raise ProtectedFileError("Документ защищён паролем. Снимите пароль и загрузите файл снова.")
    if _utf16("WordDocument") in data:
        return FileKind.DOC
    raise UnsupportedFileError(UNSUPPORTED_MESSAGE)
