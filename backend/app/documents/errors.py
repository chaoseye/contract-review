class DocumentError(Exception):
    """Ошибка чтения документа, понятная менеджеру. Текст показывается в интерфейсе как есть.

    В сообщении не должно быть фрагментов самого договора: оно попадает в базу и в логи.
    """


class UnsupportedFileError(DocumentError):
    pass


class DamagedFileError(DocumentError):
    pass


class ProtectedFileError(DocumentError):
    pass


class ToolMissingError(DocumentError):
    """На сервере не установлена нужная программа (LibreOffice, Tesseract)."""


class ConversionError(DocumentError):
    pass
