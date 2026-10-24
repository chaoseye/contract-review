"""Шифрование ключей подключений ИИ (Fernet, ключ ENCRYPTION_KEY из .env).

В базе ключ лежит только зашифрованным. Без ENCRYPTION_KEY ключ нельзя ни сохранить, ни прочитать.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


class SecretKeyError(RuntimeError):
    """ENCRYPTION_KEY не задан, повреждён или не подходит к сохранённому ключу. Текст — для администратора."""


def _fernet(settings: Settings) -> Fernet:
    key = settings.encryption_key.strip()
    if not key:
        raise SecretKeyError(
            "Не задан ENCRYPTION_KEY: без него ключи подключений ИИ не сохраняются. "
            "Создайте .env командой python deploy/scripts/init_env.py или задайте ключ вручную."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretKeyError(
            "ENCRYPTION_KEY повреждён: нужен ключ Fernet — 32 байта в base64url (44 символа)."
        ) from exc


def encrypt_secret(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(settings: Settings, token: str) -> str:
    fernet = _fernet(settings)
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise SecretKeyError(
            "Сохранённый ключ подключения не расшифровывается: ENCRYPTION_KEY изменился. Введите ключ заново."
        ) from exc


def mask_secret(value: str) -> str:
    """Для показа: только последние 4 символа."""
    if not value:
        return ""
    return "…" + value[-4:] if len(value) > 8 else "…"
