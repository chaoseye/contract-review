"""Восстановление автонумерации Word по numbering.xml.

В тексте docx номера автоматических списков не хранятся: Word вычисляет их при показе. Без этого
ссылки на пункты («п. 9.2») были бы неверными. Модуль повторяет правила Word:

- счётчики общие для всех списков (w:num), которые ссылаются на одно определение (w:abstractNum);
- w:startOverride в w:num начинает нумерацию заново при первом абзаце этого списка;
- после абзаца уровня L уровни ниже начинаются заново, если w:lvlRestart не запрещает этого;
- w:isLgl выводит все уровни арабскими цифрами («1.1» даже при римском верхнем уровне).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

LEVELS = 9
# Пределы против испорченного или нарочно собранного файла: огромный w:start строит римские цифры и
# буквы миллионами символов, и разбор не кончается. В Word «Начать с» принимает 0…32767.
MAX_START = 32767
# Номер больше этого показывается цифрами: длина буквенной метки растёт вместе с номером.
MAX_FORMATTED = 32767
MAX_LEVEL_TEXT = 255
MAX_LABEL = 50

RUSSIAN_LOWER = "абвгдежзиклмнопрстуфхцчшщэюя"
ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
         (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))  # fmt: skip


@dataclass
class LevelDef:
    start: int = 1
    fmt: str = "decimal"
    text: str = "%1."
    # Значение w:lvlRestart: None — по умолчанию, 0 — никогда не начинать заново.
    restart: int | None = None
    is_lgl: bool = False
    pstyle: str | None = None


@dataclass
class AbstractNum:
    levels: dict[int, LevelDef] = field(default_factory=dict)
    # w:numStyleLink: определение берётся из стиля нумерации.
    num_style_link: str | None = None


@dataclass
class LevelOverride:
    start: int | None = None
    level: LevelDef | None = None


@dataclass
class NumInstance:
    abstract_id: str
    overrides: dict[int, LevelOverride] = field(default_factory=dict)


@dataclass
class NumberingDefinitions:
    abstracts: dict[str, AbstractNum] = field(default_factory=dict)
    nums: dict[str, NumInstance] = field(default_factory=dict)
    # Стиль нумерации → numId (для w:numStyleLink).
    style_nums: dict[str, str] = field(default_factory=dict)


@dataclass
class NumberLabel:
    text: str
    is_bullet: bool
    level: int


def _letters(value: int, alphabet: str) -> str:
    # Word после последней буквы удваивает: z, aa, bb…
    if value < 1:
        return str(value)
    index = (value - 1) % len(alphabet)
    repeat = (value - 1) // len(alphabet) + 1
    return alphabet[index] * repeat


def _roman(value: int) -> str:
    if value < 1:
        return str(value)
    result = []
    for number, symbol in ROMAN:
        while value >= number:
            result.append(symbol)
            value -= number
    return "".join(result)


FORMATTERS: dict[str, Callable[[int], str]] = {
    "decimal": str,
    "decimalZero": lambda n: f"{n:02d}",
    "lowerLetter": lambda n: _letters(n, "abcdefghijklmnopqrstuvwxyz"),
    "upperLetter": lambda n: _letters(n, "abcdefghijklmnopqrstuvwxyz").upper(),
    "lowerRoman": _roman,
    "upperRoman": lambda n: _roman(n).upper(),
    "russianLower": lambda n: _letters(n, RUSSIAN_LOWER),
    "russianUpper": lambda n: _letters(n, RUSSIAN_LOWER).upper(),
    "none": lambda _n: "",
}


def clamp_start(value: int) -> int:
    """Начальный номер списка в пределах Word."""
    return max(0, min(MAX_START, value))


def format_number(value: int, fmt: str) -> str:
    # Редкие форматы (ordinalText, chicago, японские) показываем цифрами — для ссылок этого достаточно.
    if value > MAX_FORMATTED and fmt != "none":
        return str(value)
    return FORMATTERS.get(fmt, str)(value)


class NumberingState:
    """Счётчики списков в порядке следования абзацев документа."""

    def __init__(self, definitions: NumberingDefinitions) -> None:
        self.definitions = definitions
        self._counters: dict[str, list[int | None]] = {}
        self._restart_values: dict[tuple[str, int], int] = {}
        self._seen_nums: set[str] = set()

    def resolve_abstract(self, num_id: str) -> tuple[str, AbstractNum, NumInstance] | None:
        num = self.definitions.nums.get(num_id)
        if num is None:
            return None
        abstract_id = num.abstract_id
        abstract = self.definitions.abstracts.get(abstract_id)
        # numStyleLink: определение живёт в другом abstractNum, на который указывает стиль.
        for _ in range(5):
            if abstract is None or abstract.num_style_link is None:
                break
            linked_num = self.definitions.style_nums.get(abstract.num_style_link)
            linked = self.definitions.nums.get(linked_num) if linked_num else None
            if linked is None:
                break
            abstract_id = linked.abstract_id
            abstract = self.definitions.abstracts.get(abstract_id)
        if abstract is None:
            return None
        return abstract_id, abstract, num

    def level_def(self, num: NumInstance, abstract: AbstractNum, level: int) -> LevelDef:
        override = num.overrides.get(level)
        if override and override.level is not None:
            return override.level
        return abstract.levels.get(level) or LevelDef(text=f"%{level + 1}.")

    def level_for_style(self, num_id: str, style_id: str | None) -> int | None:
        """Уровень списка, привязанный к стилю абзаца через w:pStyle внутри w:lvl."""
        if style_id is None:
            return None
        resolved = self.resolve_abstract(num_id)
        if resolved is None:
            return None
        _, abstract, _ = resolved
        for level, definition in abstract.levels.items():
            if definition.pstyle == style_id:
                return level
        return None

    def next_label(self, num_id: str, level: int) -> NumberLabel | None:
        if num_id in {"", "0"}:
            return None
        resolved = self.resolve_abstract(num_id)
        if resolved is None:
            return None
        abstract_id, abstract, num = resolved
        level = max(0, min(LEVELS - 1, level))
        counters = self._counters.setdefault(abstract_id, [None] * LEVELS)

        if num_id not in self._seen_nums:
            self._seen_nums.add(num_id)
            for override_level, override in num.overrides.items():
                if override.start is not None:
                    counters[override_level] = None
                    self._restart_values[(abstract_id, override_level)] = override.start

        definition = self.level_def(num, abstract, level)
        current = counters[level]
        if current is None:
            current = self._restart_values.pop((abstract_id, level), definition.start)
        else:
            current += 1
        counters[level] = current

        for deeper in range(level + 1, LEVELS):
            restart = self.level_def(num, abstract, deeper).restart
            # lvlRestart=n: начинать заново после уровня n (с единицы); 0 — никогда.
            if restart is None or (restart != 0 and level < restart):
                counters[deeper] = None

        if definition.fmt == "bullet":
            return NumberLabel(text=definition.text[:MAX_LABEL], is_bullet=True, level=level)
        text = self._render(abstract_id, num, abstract, definition)
        return NumberLabel(text=text[:MAX_LABEL], is_bullet=False, level=level)

    def _render(self, abstract_id: str, num: NumInstance, abstract: AbstractNum, current: LevelDef) -> str:
        counters = self._counters[abstract_id]
        text = current.text
        for placeholder_level in range(LEVELS - 1, -1, -1):
            token = f"%{placeholder_level + 1}"
            if token not in text:
                continue
            definition = self.level_def(num, abstract, placeholder_level)
            value = counters[placeholder_level]
            if value is None:
                value = definition.start
            fmt = "decimal" if current.is_lgl else definition.fmt
            text = text.replace(token, format_number(value, fmt))
        return text
