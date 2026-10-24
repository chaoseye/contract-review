"""Данные нашей организации: для протокола и сверки реквизитов.

Их вносит администратор в разделе «Организация» или команда `python -m app.cli org-set`. Значения берутся
из карточки организации и хранятся только в базе — в репозиторий они не попадают.
"""

import io
import re

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Organization

REQUISITE_KEYS = (
    "inn",
    "ogrnip",
    "legal_address",
    "postal_address",
    "actual_address",
    "bank",
    "bik",
    "account",
    "corr_account",
    "email",
    "email_domains",
    "phone",
)
REQUISITE_LABELS = {
    "inn": "ИНН",
    "ogrnip": "ОГРНИП",
    "legal_address": "Юридический адрес",
    "postal_address": "Почтовый адрес",
    "actual_address": "Фактический адрес",
    "bank": "Банк",
    "bik": "БИК",
    "account": "Расчётный счёт",
    "corr_account": "Корреспондентский счёт",
    "email": "E-mail",
    "email_domains": "Домены e-mail",
    "phone": "Телефон",
}
# Сколько цифр в числовых реквизитах: ИНН ИП — 12, юрлица — 10; ОГРНИП — 15, ОГРН — 13.
REQUISITE_DIGITS = {"inn": (10, 12), "ogrnip": (13, 15), "bik": (9,), "account": (20,), "corr_account": (20,)}
MAX_REQUISITE_LENGTH = 500
ASSETS = {"signature": "signature_png", "stamp": "stamp_png", "header": "header_png"}
ASSET_LABELS = {"signature": "подпись", "stamp": "печать", "header": "шапка бланка"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class OrganizationError(ValueError):
    """Неверные данные организации. Текст — для администратора."""


def get_organization(db: Session) -> Organization | None:
    return db.scalar(select(Organization).order_by(Organization.id).limit(1))


def ensure_organization(db: Session) -> Organization:
    organization = get_organization(db)
    if organization is None:
        organization = Organization(
            full_name="",
            short_name="",
            signer="",
            representative="",
            basis="",
            default_city="",
            requisites={},
        )
        organization.footer_text = ""
        db.add(organization)
        db.flush()
    return organization


def set_requisites(organization: Organization, values: dict[str, str]) -> None:
    unknown = sorted(set(values) - set(REQUISITE_KEYS))
    if unknown:
        raise OrganizationError(
            f"Неизвестные реквизиты: {', '.join(unknown)}. Допустимы: {', '.join(REQUISITE_KEYS)}."
        )
    requisites = dict(organization.requisites or {})
    for key, value in values.items():
        if value.strip():
            requisites[key] = value.strip()
        else:
            requisites.pop(key, None)
    # Формат проверяется так же, как в админке: сверке нужны числа без ошибок.
    organization.requisites = validate_requisites(requisites)


# Домен e-mail: «example.ru», «почта.рф»; @ в начале допускается и отбрасывается.
_DOMAIN = re.compile(r"(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}")


def email_domains(value: str) -> list[str]:
    """Домены из поля «Домены e-mail»: через запятую, точку с запятой или пробел."""
    parts = re.split(r"[\s,;]+", value.strip().lower())
    return list(dict.fromkeys(part.lstrip("@") for part in parts if part.lstrip("@")))


def validate_requisites(values: dict[str, str]) -> dict[str, str]:
    """Проверить формат реквизитов и вернуть их без пустых. Числа хранятся без пробелов."""
    unknown = sorted(set(values) - set(REQUISITE_KEYS))
    if unknown:
        raise OrganizationError(
            f"Неизвестные реквизиты: {', '.join(unknown)}. Допустимы: {', '.join(REQUISITE_KEYS)}."
        )
    result: dict[str, str] = {}
    for key in REQUISITE_KEYS:
        value = " ".join(str(values.get(key) or "").split())
        if not value:
            continue
        label = REQUISITE_LABELS[key]
        if len(value) > MAX_REQUISITE_LENGTH:
            raise OrganizationError(f"{label}: не длиннее {MAX_REQUISITE_LENGTH} символов.")
        if key in REQUISITE_DIGITS:
            digits = value.replace(" ", "")
            lengths = REQUISITE_DIGITS[key]
            if not digits.isdigit() or len(digits) not in lengths:
                expected = " или ".join(str(length) for length in lengths)
                raise OrganizationError(f"{label}: нужно {expected} цифр.")
            value = digits
        elif key == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise OrganizationError("E-mail указан неверно.")
        elif key == "email_domains":
            domains = email_domains(value)
            wrong = [domain for domain in domains if not _DOMAIN.fullmatch(domain)]
            if wrong:
                raise OrganizationError(f"{label}: «{wrong[0]}» — не домен. Пример: example.ru.")
            value = ", ".join(domains)
        result[key] = value
    return result


def load_image(data: bytes) -> bytes:
    """Проверить картинку (PNG или JPEG) и вернуть её как есть."""
    if len(data) > MAX_IMAGE_BYTES:
        raise OrganizationError("Картинка больше 5 МБ.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            kind = image.format
    except (UnidentifiedImageError, OSError) as exc:
        raise OrganizationError("Это не картинка PNG или JPEG.") from exc
    if kind not in ("PNG", "JPEG"):
        raise OrganizationError("Нужна картинка PNG или JPEG.")
    return data


def set_asset(organization: Organization, kind: str, data: bytes | None) -> None:
    if kind not in ASSETS:
        raise OrganizationError(f"Неизвестная картинка: {kind}. Допустимы: {', '.join(ASSETS)}.")
    setattr(organization, ASSETS[kind], load_image(data) if data is not None else None)
