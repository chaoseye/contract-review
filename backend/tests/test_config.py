import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_placeholder_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="заглушка"):
        Settings(_env_file=None, app_secret_key="change-me")  # type: ignore[call-arg]


def test_short_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_secret_key="short")  # type: ignore[call-arg]


def test_secure_cookies_follow_public_url() -> None:
    secret = "x" * 40
    assert Settings(_env_file=None, app_secret_key=secret, public_url="https://a.ru").secure_cookies  # type: ignore[call-arg]
    assert not Settings(
        _env_file=None, app_secret_key=secret, public_url="http://localhost:8080"
    ).secure_cookies  # type: ignore[call-arg]
