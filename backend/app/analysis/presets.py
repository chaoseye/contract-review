"""Готовые адреса подключений ИИ (documentation/ARCHITECTURE.md, раздел 5).

Отдельный лёгкий модуль: его импортирует CLI, не загружая SDK openai и anthropic.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    title: str
    base_url: str
    needs_key: bool
    russia_only: bool = False
    # Тип подключения (LlmKind): openai_compat или anthropic.
    kind: str = "openai_compat"
    # Глубина рассуждений для нового подключения (только anthropic): анализ договора — сложная задача.
    effort: str | None = None


# «Только серверы в РФ» у RouterAI по умолчанию выключена. Если договоры нельзя отправлять зарубежным
# моделям, администратор включает её в подключении.
PRESETS: dict[str, Preset] = {
    "routerai": Preset("RouterAI", "https://routerai.ru/api/v1", needs_key=True),
    "openrouter": Preset("OpenRouter", "https://openrouter.ai/api/v1", needs_key=True),
    "openai": Preset("OpenAI", "https://api.openai.com/v1", needs_key=True),
    # API Anthropic: свой формат запросов, не совместимый с OpenAI.
    "anthropic": Preset(
        "Anthropic", "https://api.anthropic.com", needs_key=True, kind="anthropic", effort="high"
    ),
    # Из контейнера локальная машина доступна как host.docker.internal.
    "ollama": Preset("Ollama", "http://host.docker.internal:11434/v1", needs_key=False),
}
