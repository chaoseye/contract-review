"""Пароли и токены сессий."""

import hashlib
import hmac
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Проверка пароля против фиктивного хеша.

    Нужна, когда пользователя нет: ответ занимает столько же времени, сколько при неверном пароле,
    и по времени нельзя понять, существует ли логин.
    """
    verify_password(_dummy_hash(), password)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str, secret_key: str) -> str:
    """В базе хранится только HMAC токена: утечка таблицы sessions не даёт войти."""
    return hmac.new(secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
