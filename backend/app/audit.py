"""Записи журнала действий. Только факты и числа — тексты договоров сюда не пишутся."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEntry, Check

# Действия в журнале. Подписи — для раздела «Журнал» в админке и выгрузки CSV.
ACTION_LABELS = {
    "upload": "загрузка договора",
    "analysis_done": "анализ выполнен",
    "analysis_failed": "анализ не выполнен",
    "protocol_docx": "протокол Word",
    "protocol_pdf": "протокол PDF",
    "delete": "удаление проверки",
    "user_create": "пользователь создан",
    "user_update": "пользователь изменён",
    "user_password": "сброс пароля",
    "prompt_create": "новая версия промпта",
    "prompt_activate": "смена версии промпта",
    "prompt_test": "прогон черновика промпта",
    "llm_create": "подключение ИИ добавлено",
    "llm_update": "подключение ИИ изменено",
    "llm_activate": "смена подключения ИИ",
    "llm_delete": "подключение ИИ удалено",
    "llm_test": "проверка связи с ИИ",
    "codex_login": "вход в Codex",
    "codex_logout": "выход из Codex",
    "org_update": "данные организации",
    "org_asset": "подпись, печать или шапка",
    "template_update": "шаблон протокола",
    "settings_update": "настройки",
    "settings_export": "выгрузка настроек",
    "settings_import": "загрузка настроек",
}


def record(
    db: Session, action: str, *, user_id: int | None, check: Check | None = None, **fields: Any
) -> None:
    """Добавить запись в журнал. Контрагент и номер договора берутся из протокола или ответа модели."""
    if check is not None:
        counterparty, number = contract_identity(check)
        fields.setdefault("counterparty", counterparty)
        fields.setdefault("contract_number", number)
    entry = AuditEntry(
        action=action,
        user_id=user_id,
        check_id=check.id if check is not None else fields.pop("check_id", None),
        **{key: value for key, value in fields.items() if key != "check_id"},
    )
    if entry.counterparty:
        entry.counterparty = entry.counterparty[:500]
    if entry.contract_number:
        entry.contract_number = entry.contract_number[:100]
    if entry.details:
        entry.details = entry.details[:500]
    db.add(entry)


def contract_identity(check: Check) -> tuple[str | None, str | None]:
    """Контрагент и номер договора: сначала то, что поправил менеджер в протоколе, потом ответ модели."""
    meta = (check.protocol or {}).get("meta") or {}
    contract = ((check.result or {}).get("answer") or {}).get("contract") or {}
    buyer = contract.get("buyer") or {}
    counterparty = meta.get("buyer_full_name") or buyer.get("name")
    number = meta.get("contract_number") or contract.get("number")
    return counterparty or None, number or None
