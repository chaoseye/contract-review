"""Настройки из базы (лимиты, шаблон протокола) и версии промпта на уровне модулей."""

from datetime import date

import pytest
from sqlalchemy import update

from app import runtime
from app.analysis import prompts
from app.analysis.default_prompt import DEFAULT_ADMIN_PROMPT
from app.models import PromptVersion, SettingEntry
from app.protocol.templates import ProtocolTemplate, render_closing, render_preamble


def test_limits_default_to_env_and_survive_broken_value(db_factory, settings) -> None:
    with db_factory() as db:
        assert runtime.get_limits(db, settings).model_dump() == {"check_ttl_hours": 24, "max_upload_mb": 50}
        runtime.set_limits(db, runtime.Limits(check_ttl_hours=6, max_upload_mb=10), None)
        db.commit()
        assert runtime.get_limits(db, settings).max_upload_bytes == 10 * 1024 * 1024
        # Испорченное значение не останавливает работу: действуют значения из .env.
        db.get(SettingEntry, runtime.LIMITS_KEY).value = {"check_ttl_hours": -5}  # type: ignore[union-attr]
        db.commit()
        assert runtime.get_limits(db, settings).check_ttl_hours == 24
        db.add(SettingEntry(key=runtime.TEMPLATE_KEY, value={"preamble": "{unknown}"}))
        db.commit()
        assert runtime.get_template(db) == ProtocolTemplate()


def test_template_rendering_with_placeholders() -> None:
    template = ProtocolTemplate(
        preamble="{buyer_intro}; {supplier_intro}; {contract_kind} № {contract_number} от {contract_date}",
        closing_items=["Договор {contract_kind} № {contract_number} от {contract_date}."],
        appendices_item="Приложено: {appendices}",
    )
    text = render_preamble(
        template, "Покупатель А", "Поставщик Б", "к договору", False, "поставки", "7", date(2026, 1, 2)
    )
    assert text == "Покупатель А; Поставщик Б; поставки № 7 от 02.01.2026"
    closing = render_closing(template, "поставки", "7", date(2026, 1, 2), ["Спецификация", " "])
    assert closing == ["Договор поставки № 7 от 02.01.2026.", "Приложено: Спецификация"]
    with pytest.raises(ValueError, match="Неизвестная подстановка"):
        ProtocolTemplate(appendices_item="{contract}")


def test_prompt_versions(db_factory) -> None:
    with db_factory() as db:
        first = prompts.active_version(db)
        assert first.text == DEFAULT_ADMIN_PROMPT and first.is_active
        with pytest.raises(prompts.PromptError, match="пустой"):
            prompts.create_version(db, "   ", "", None)
        with pytest.raises(prompts.PromptError, match="длиннее"):
            prompts.create_version(db, "x" * (prompts.MAX_PROMPT_LENGTH + 1), "", None)
        second = prompts.create_version(db, "Второй", "", None)
        assert second.is_active and not first.is_active
        # Если активной версии нет (например, после ручной правки базы), активируется последняя.
        db.execute(update(PromptVersion).values(is_active=False))
        db.flush()
        assert prompts.active_version(db).id == second.id
        assert [version.id for version in prompts.list_versions(db)] == [second.id, first.id]
