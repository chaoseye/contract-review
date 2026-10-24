"""Наша сторона в договоре и сверка наших реквизитов без ИИ (ARCHITECTURE.md, раздел 4.1).

Организация ищется по ИНН и наименованию из карточки. Роль берётся из преамбулы («именуемый в дальнейшем
«Поставщик»»), а если там её нет — из заголовка блока реквизитов («ПОСТАВЩИК:»). Если реквизиты стоят
таблицей, блок реквизитов — колонка таблицы с нашим ИНН. Реквизиты сравниваются с карточкой: числа
точно, адреса — по номерам и нечётко по словам. Сверка считается заново при каждом открытии проверки,
поэтому правка карточки сразу видна. В модель уходит только роль, без реквизитов.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.analysis.prompt import CheckInfo
from app.models import Check, Organization
from app.organization import REQUISITE_LABELS, email_domains, get_organization

Role = Literal["supplier", "buyer", "other", "unknown"]
Status = Literal["match", "mismatch", "not_found"]

ROLE_WORDS: dict[str, Role] = {
    "поставщик": "supplier",
    "продавец": "supplier",
    "покупатель": "buyer",
    "заказчик": "buyer",
    "исполнитель": "other",
    "подрядчик": "other",
    "агент": "other",
    "принципал": "other",
    "комиссионер": "other",
    "комитент": "other",
    "арендатор": "other",
    "арендодатель": "other",
    "лицензиар": "other",
    "лицензиат": "other",
}
_ROLES = "|".join(ROLE_WORDS)
# «…, именуемый в дальнейшем «Поставщик»», «(далее — «Поставщик»)», «(«Поставщик»)».
_ROLE_AFTER_NAME = re.compile(
    rf"(?:именуем\w*|далее)\s*(?:в\s+дальнейшем\s*)?(?:по\s+тексту\s*)?[—–-]?\s*[«\"“'(]*\s*({_ROLES})\b"
    rf"|[«\"“(]\s*({_ROLES})\s*[»\"”)]"
)
_ROLE_HEADER = re.compile(rf"^\s*({_ROLES})\s*(?::|$|\n)")
_PARTY_END = re.compile(r"с\s+(?:одной|другой)\s+стороны")

LEGAL_FORMS = (
    "индивидуальный предприниматель",
    "общество с ограниченной ответственностью",
    "публичное акционерное общество",
    "непубличное акционерное общество",
    "закрытое акционерное общество",
    "открытое акционерное общество",
    "акционерное общество",
    "ип",
    "ооо",
    "пао",
    "зао",
    "оао",
    "ао",
)

# Банк не сверяется: его название пишут по-разному, а счёт и БИК сверяются точно.
# Домены e-mail — не отдельный реквизит, а часть сверки e-mail.
LABELS = {key: label for key, label in REQUISITE_LABELS.items() if key not in ("bank", "email_domains")}

_DIGITS20 = r"(\d(?:\s?\d){19})"
_NUMBER_PATTERNS = {
    "inn": re.compile(r"(?<!\w)инн(?:\s*/\s*кпп)?\s*:?\s*(\d{12}|\d{10})(?!\d)"),
    "ogrnip": re.compile(r"(?<!\w)огрн(?:ип)?\s*:?\s*(\d{15}|\d{13})(?!\d)"),
    "bik": re.compile(r"(?<!\w)бик\s*:?\s*(\d{9})(?!\d)"),
    # «р/с», «р/счёт», «расчётный счёт», «Номер расчётного счёта»; «корр/счёт» — не расчётный счёт.
    "account": re.compile(
        r"(?<!\w)(?:р/с(?:ч(?:[её]т)?)?|р\.\s?с\.|расч[её]тн\w*\s+сч[её]т\w*|сч[её]т\w*\s+получател\w*)"
        rf"\s*(?:№\s*)?:?\s*{_DIGITS20}"
    ),
    "corr_account": re.compile(
        r"(?<!\w)(?:к/с(?:ч(?:[её]т)?)?|к\.\s?с\.|корр?[./]?\s*сч(?:[её]т\w*|\.)?|корреспондентск\w*\s+сч[её]т\w*)"
        rf"\s*(?:№\s*)?:?\s*{_DIGITS20}"
    ),
}
_ADDRESS_LABELS = {
    "legal_address": re.compile(
        r"(?:юр(?:идический)?\.?\s*адрес|адрес\s+(?:места\s+нахождения|регистрации)|место\s+нахождения)\s*:?"
    ),
    "postal_address": re.compile(r"почт(?:овый)?\.?\s*адрес\s*:?"),
    "actual_address": re.compile(r"факт(?:ический)?\.?\s*адрес\s*:?"),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)?[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
_PHONE_LINE = re.compile(r"тел|моб|факс|phone|\+7")
# Начало строки с другим реквизитом или подписью: адрес, разбитый на строки ячейки, на ней кончается.
_OTHER_LABEL = re.compile(
    r"\s*(?:"
    r"(?:инн|кпп|огрн|огрнип|бик|окпо|октмо|окато|оквэд|окопф|окфс|окогу|кбк|уфк|мп)(?!\w)"
    r"|[ркл]\s?/\s?с|[рк]\.\s?с\.|корр?[./]?\s*сч|сч[её]т|расч[её]тн|корреспондентск|лицев|казначейск"
    r"|банк(?!\w)|отделени|наименовани|получател|дата|свидетельств|паспорт|выдан"
    r"|тел(?:\.|:|\s|ефон)|моб(?:\.|:|\s|ильн)|факс|e-?\s?mail|эл\.|электронн|почта(?!\w)|сайт"
    r"|адрес|место\s+нахождения|(?:юр|почт|факт)\w*\.?\s*адрес"
    r"|руководител|директор|подпис|м\.\s?п\.|_{3}"
    rf"|(?:{'|'.join(LEGAL_FORMS)})(?!\w)"
    # «Наименование банка:», «Получатель:» и любая другая короткая метка с двоеточием.
    r"|[^\W\d_]+(?:[\s.-]+[^\W\d_]+){0,3}\s*:"
    r")"
)
_LONG_NUMBER = re.compile(r"\d{9,}")
_DATE = re.compile(r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{4}(?!\d)")
_SCAN_BACK = 8
_SCAN_FORWARD = 16
_PREAMBLE_BLOCKS = 20
ADDRESS_SIMILARITY = 85


@dataclass
class RequisiteCheck:
    key: str
    label: str
    expected: str
    found: list[str]
    status: Status


@dataclass
class SideReport:
    """Итог для экрана проверки (поле requisites) и строка для запроса к модели."""

    status: Literal["found", "not_found", "skipped"]
    role: Role = "unknown"
    # Как нас назвали в договоре: «Поставщик», «Покупатель»…
    role_word: str | None = None
    found_by: list[str] = field(default_factory=list)
    preamble_block: str | None = None
    requisites_block: str | None = None
    checks: list[RequisiteCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_note(self) -> str | None:
        """Строка «Сведений о проверке»: роль без самих реквизитов."""
        if self.status == "skipped":
            return None
        if self.status == "not_found":
            return (
                "Нашу организацию по ИНН и наименованию в договоре найти не удалось: "
                "проверь, о какой стороне идёт речь."
            )
        if self.role_word:
            return (
                "По ИНН и наименованию наша организация найдена в договоре, "
                f"её роль по тексту — «{self.role_word}»."
            )
        return (
            "Наша организация найдена в договоре по ИНН или наименованию, но её роль по тексту не определена."
        )


def _norm(text: str) -> str:
    """Нижний регистр, «е» вместо «ё», обычные пробелы. Длина строки не меняется: по позициям в ней
    найденные значения берутся из исходного текста, как они написаны в договоре."""
    return text.replace("\u00a0", " ").replace("ё", "е").replace("Ё", "Е").lower()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _core_words(name: str) -> list[str]:
    text = _norm(name)
    quoted = re.findall(r"[«\"“]([^»\"”]+)[»\"”]", text)
    if quoted:
        return [word for word in re.findall(r"[\w-]+", quoted[0])]
    for form in LEGAL_FORMS:
        text = re.sub(rf"(?<!\w){re.escape(form)}(?!\w)", " ", text)
    return re.findall(r"[^\W\d_][\w-]*", text)


def name_patterns(organization: Organization) -> list[re.Pattern[str]]:
    """Наименование целиком и, для ИП, «Фамилия И.О.» и «И.О. Фамилия»."""
    patterns: list[re.Pattern[str]] = []
    seen: set[str] = set()
    for name in (organization.full_name, organization.short_name):
        words = _core_words(name)
        if not words or (len(words) == 1 and len(words[0]) < 4):
            continue
        variants = [r"\s+".join(re.escape(word) for word in words)]
        if len(words) == 3:
            surname, first, middle = words
            initials = rf"{first[0]}\.\s*{middle[0]}\."
            variants += [rf"{re.escape(surname)}\s+{initials}", rf"{initials}\s*{re.escape(surname)}"]
        for variant in variants:
            if variant not in seen:
                seen.add(variant)
                patterns.append(re.compile(rf"(?<!\w){variant}(?!\w)"))
    return patterns


def _expected(organization: Organization) -> dict[str, str]:
    requisites = organization.requisites or {}
    values = {key: str(requisites.get(key) or "").strip() for key in LABELS}
    # E-mail сверяется и тогда, когда в карточке есть только домены: адрес на них — наш.
    has_domains = bool(email_domains(str(requisites.get("email_domains") or "")))
    return {key: value for key, value in values.items() if value or (key == "email" and has_domains)}


def _party_window(text: str, start: int) -> tuple[int, int]:
    """Описание нашей стороны в преамбуле: от наименования до «с одной (другой) стороны»."""
    window = text[start : start + 500]
    end = _PARTY_END.search(window)
    return start, start + (end.start() if end else len(window))


def _role_in(window: str) -> str | None:
    match = _ROLE_AFTER_NAME.search(window)
    return (match.group(1) or match.group(2)) if match else None


def _numbers(key: str, text: str) -> list[str]:
    return [_digits(match.group(1)) for match in _NUMBER_PATTERNS[key].finditer(text)]


def _line_end(text: str, position: int) -> int:
    end = text.find("\n", position)
    return len(text) if end < 0 else end


def _address_values(key: str, text: str, original: str) -> list[str]:
    values = []
    for match in _ADDRESS_LABELS[key].finditer(text):
        value = original[match.end() : _line_end(text, match.end())].strip(" .;,")
        # Заготовки вида «Почтовый адрес» без цифр — не заполненный адрес.
        if re.search(r"\d", value):
            values.append(value)
    return values


def _address_matches(expected: str, found: str) -> bool:
    expected_n, found_n = _norm(expected), _norm(found)
    if re.findall(r"\d+", expected_n) != re.findall(r"\d+", found_n):
        return False
    words = re.compile(r"[^\W\d_]+")
    left = " ".join(words.findall(expected_n))
    right = " ".join(words.findall(found_n))
    return fuzz.token_set_ratio(left, right) >= ADDRESS_SIMILARITY


def _phone_key(text: str) -> str:
    return _digits(text)[-10:]


def _phones(text: str, original: str) -> list[str]:
    found = []
    for match in _PHONE.finditer(text):
        line = text[text.rfind("\n", 0, match.start()) + 1 : _line_end(text, match.end())]
        # Только строки с телефоном: иначе за телефон сошёл бы ИНН юрлица из десяти цифр.
        if _PHONE_LINE.search(line):
            found.append(original[match.start() : match.end()].strip())
    return found


def _compare(
    key: str, expected: str, text: str, original: str, domains: tuple[str, ...] = ()
) -> RequisiteCheck:
    label = LABELS[key]
    if key in _NUMBER_PATTERNS:
        found = list(dict.fromkeys(_numbers(key, text)))
        status: Status = "not_found" if not found else ("match" if _digits(expected) in found else "mismatch")
    elif key in _ADDRESS_LABELS:
        found = list(dict.fromkeys(_address_values(key, text, original)))
        if not found:
            status = "not_found"
        else:
            status = "match" if any(_address_matches(expected, value) for value in found) else "mismatch"
    elif key == "email":
        emails = [(match.group(0), original[match.start() : match.end()]) for match in _EMAIL.finditer(text)]
        found = list(dict.fromkeys(shown.rstrip(".") for _, shown in emails))

        def ours(address: str) -> bool:
            address = address.rstrip(".")
            return address == expected.lower() or address.rsplit("@", 1)[-1] in domains

        # Наши — адрес из карточки и любые адреса на доменах организации. Чужой адрес в нашем
        # блоке реквизитов — расхождение, даже если рядом есть и правильный.
        status = (
            "not_found" if not found else ("match" if all(ours(value) for value, _ in emails) else "mismatch")
        )
        expected = "; ".join(
            [*([expected] if expected else []), *(f"любой адрес на {domain}" for domain in domains)]
        )
    else:  # phone
        found = list(dict.fromkeys(_phones(text, original)))
        ours = {_phone_key(part) for part in re.split(r"[,;]", expected) if len(_phone_key(part)) == 10}
        if not found:
            status = "not_found"
        else:
            status = "match" if any(_phone_key(value) in ours for value in found) else "mismatch"
    return RequisiteCheck(key=key, label=label, expected=expected, found=found, status=status)


Cell = tuple[int, int, int]


def _cell(value: Any) -> Cell | None:
    """Ячейка блока (таблица, строка, колонка): у свежего разбора — кортеж, из базы — список."""
    if isinstance(value, list | tuple) and len(value) == 3:
        try:
            return int(value[0]), int(value[1]), int(value[2])
        except (TypeError, ValueError):
            return None
    return None


def _region(
    texts: list[str], kinds: list[str], cells: list[Cell | None], anchor: int, our_inn: str
) -> list[int]:
    """Блоки реквизитов нашей стороны вокруг найденного ИНН или наименования.

    Если этот блок — ячейка таблицы, просматривается вся её колонка: реквизиты сторон часто стоят
    таблицей в две колонки, и по строкам ячейки поставщика идут вперемешку с ячейками покупателя.
    Иначе — соседние блоки. Блок кончается на заголовке другой стороны, чужом ИНН или следующем разделе.
    Таблица бывает и «сторона в строке»: в одной колонке «Поставщик:» и «Покупатель:», в другой —
    реквизиты. Тогда строка таблицы с заголовком стороны в соседней ячейке — начало блока другой стороны.
    """
    cell = cells[anchor]
    # Строка таблицы для каждого блока последовательности; вне таблицы строк нет.
    rows: list[int] | None = None
    # Строки таблицы, где в другой колонке стоит заголовок стороны.
    party_rows: set[int] = set()
    if cell is None:
        sequence = list(range(len(texts)))
        back, forward = _SCAN_BACK, _SCAN_FORWARD
    else:
        same_column = (cell[0], cell[2])
        column = [
            (index, other)
            for index, other in enumerate(cells)
            if other and (other[0], other[2]) == same_column
        ]
        sequence = [index for index, _ in column]
        rows = [other[1] for _, other in column]
        back = forward = len(sequence)
        party_rows = {
            other[1]
            for index, other in enumerate(cells)
            if other and other[0] == cell[0] and other[2] != cell[2] and _ROLE_HEADER.match(texts[index])
        }
    position = sequence.index(anchor)
    # Найдены по наименованию — ИНН в договоре может быть нашим с ошибкой, и чужим его не считаем.
    by_inn = bool(our_inn) and our_inn in _numbers("inn", texts[anchor])

    start = position
    for pos in range(position - 1, max(-1, position - back), -1):
        index = sequence[pos]
        if rows is not None and rows[pos] != rows[pos + 1] and rows[pos + 1] in party_rows:
            # Наш блок начинается строкой с заголовком стороны, выше — другая сторона.
            break
        if _ROLE_HEADER.match(texts[index]):
            start = pos
            break
        if kinds[index] == "heading":
            break
        if by_inn and any(number != our_inn for number in _numbers("inn", texts[index])):
            # Над нами другая сторона: остаток её ячейки после ИНН в сверку тоже не попадает.
            while rows is not None and start < position and rows[start] == rows[pos]:
                start += 1
            break
        if rows is not None:
            # В колонке таблицы реквизиты нашей стороны стоят и выше ИНН, даже без заголовка.
            start = pos
    end = position
    # Первый ИНН в блоке — наш (даже если с ошибкой), следующий другой ИНН — уже другой стороны.
    seen_inn = any(_numbers("inn", texts[sequence[pos]]) for pos in range(start, position + 1))
    for pos in range(position + 1, min(len(sequence), position + forward)):
        index = sequence[pos]
        text = texts[index]
        if _ROLE_HEADER.match(text) or kinds[index] == "heading":
            break
        if rows is not None and rows[pos] != rows[pos - 1] and rows[pos] in party_rows:
            # Следующая строка таблицы подписана другой стороной.
            break
        numbers = _numbers("inn", text)
        if numbers:
            if seen_inn and any(number != our_inn for number in numbers):
                break
            seen_inn = True
        end = pos
    return [sequence[pos] for pos in range(start, end + 1)]


def _continues_address(line: str) -> bool:
    """Строка продолжает адрес с предыдущей строки той же ячейки: в ней нет другого реквизита."""
    return bool(line.strip()) and not (
        _OTHER_LABEL.match(line)
        or _ROLE_HEADER.match(line)
        or _EMAIL.search(line)
        or _LONG_NUMBER.search(line)
        or _PHONE.search(line)
        or _DATE.search(line)
    )


def _region_text(
    region: list[int], texts: list[str], originals: list[str], cells: list[Cell | None]
) -> tuple[str, str]:
    """Текст блока реквизитов по строкам и тот же текст, как он написан в договоре.

    Адрес, разбитый на несколько строк одной ячейки или абзаца («Юридический адрес: 100000,» и дальше
    строки), собирается в одну строку до следующей метки: иначе сверился бы только его кусок.
    """
    units: list[list[int]] = []
    for index in region:
        if units and cells[index] is not None and cells[units[-1][-1]] == cells[index]:
            units[-1].append(index)
        else:
            units.append([index])
    lines: list[str] = []
    shown: list[str] = []
    for unit in units:
        in_address = False
        unit_lines = "\n".join(texts[index] for index in unit).split("\n")
        unit_shown = "\n".join(originals[index] for index in unit).split("\n")
        for line, original in zip(unit_lines, unit_shown, strict=True):
            if in_address and _continues_address(line):
                # Обрезка по нормализованной строке: позиции в обеих строках совпадают.
                left, right = len(line) - len(line.lstrip()), len(line.rstrip())
                lines[-1] += " " + line[left:right]
                shown[-1] += " " + original[left:right]
                continue
            lines.append(line)
            shown.append(original)
            in_address = any(label.search(line) for label in _ADDRESS_LABELS.values())
    return "\n".join(lines), "\n".join(shown)


def check_side(blocks: Iterable[Mapping[str, Any]], organization: Organization | None) -> SideReport:
    """Где мы в договоре, кем названы и совпадают ли реквизиты с карточкой организации."""
    if organization is None:
        return SideReport(status="skipped", warnings=[])
    expected = _expected(organization)
    patterns = name_patterns(organization)
    our_inn = _digits(expected.get("inn", ""))
    if not patterns and not our_inn:
        return SideReport(status="skipped")

    items = list(blocks)
    originals = [str(block.get("text") or "") for block in items]
    texts = [_norm(text) for text in originals]
    # Редкие буквы меняют длину в нижнем регистре — тогда значения показываются в нижнем регистре.
    originals = [
        orig if len(orig) == len(text) else text for orig, text in zip(originals, texts, strict=True)
    ]
    kinds = [str(block.get("kind") or "") for block in items]
    ids = [str(block.get("id") or "") for block in items]
    cells = [_cell(block.get("table")) for block in items]
    report = SideReport(status="not_found")

    # Преамбула: наименование или ИНН в первых блоках, роль — в описании нашей стороны после них.
    preamble_window = preamble_original = ""
    for index, text in enumerate(texts[:_PREAMBLE_BLOCKS]):
        match = next((found for pattern in patterns if (found := pattern.search(text))), None)
        by_inn = bool(our_inn) and our_inn in _numbers("inn", text)
        if match is None and not by_inn:
            continue
        report.preamble_block = ids[index]
        start, end = _party_window(text, match.start() if match else text.find(our_inn))
        preamble_window, preamble_original = text[start:end], originals[index][start:end]
        word = _role_in(preamble_window)
        if word:
            report.role_word = word.capitalize()
            report.role = ROLE_WORDS[word]
        break

    # Реквизиты: последний блок с нашим ИНН после преамбулы, иначе — с наименованием во второй половине
    # договора. Для ИНН половины мало: за реквизитами бывает спецификация длиннее всего договора.
    half = len(texts) // 2
    after_preamble = ids.index(report.preamble_block) + 1 if report.preamble_block else half
    anchor = None
    for index in range(len(texts) - 1, after_preamble - 1, -1):
        if our_inn and our_inn in _numbers("inn", texts[index]):
            anchor, report.found_by = index, ["inn"]
            break
    if anchor is None:
        for index in range(len(texts) - 1, half - 1, -1):
            if any(pattern.search(texts[index]) for pattern in patterns):
                anchor, report.found_by = index, ["name"]
                break
    region_text = region_original = ""
    if anchor is not None:
        region = _region(texts, kinds, cells, anchor, our_inn)
        report.requisites_block = ids[region[0]]
        region_text, region_original = _region_text(region, texts, originals, cells)
        header = _ROLE_HEADER.match(texts[region[0]])
        first = cells[region[0]]
        if header is None and first is not None:
            # «Сторона в строке»: заголовок стоит в соседней ячейке той же строки таблицы.
            header = next(
                (
                    found
                    for index, other in enumerate(cells)
                    if other and other[:2] == first[:2] and other[2] != first[2]
                    if (found := _ROLE_HEADER.match(texts[index]))
                ),
                None,
            )
        if report.role_word is None and header:
            report.role_word = header.group(1).capitalize()
            report.role = ROLE_WORDS[header.group(1)]
        if any(pattern.search(region_text) for pattern in patterns) and "name" not in report.found_by:
            report.found_by.append("name")
    if (
        report.preamble_block
        and "name" not in report.found_by
        and any(pattern.search(preamble_window) for pattern in patterns)
    ):
        report.found_by.append("name")
    if "inn" not in report.found_by and our_inn and our_inn in _numbers("inn", preamble_window):
        report.found_by.append("inn")

    if report.preamble_block is None and anchor is None:
        report.warnings.append(
            "Нашу организацию не нашли в договоре ни по ИНН, ни по наименованию. Проверьте, что договор "
            "заключается с нами, и данные в разделе «Организация»."
        )
        return report
    report.status = "found"
    if report.role in ("buyer", "other"):
        report.warnings.append(
            f"В договоре наша организация названа «{report.role_word}», а не «Поставщик». "
            "Анализ рассчитан на сторону поставщика — проверьте, тот ли это договор."
        )
    elif report.role == "unknown":
        report.warnings.append(
            "Не удалось понять, кем наша организация названа в договоре: проверьте преамбулу."
        )

    if not region_text:
        report.warnings.append("Блок реквизитов поставщика в договоре не найден: реквизиты не сверялись.")
    domains = tuple(email_domains(str((organization.requisites or {}).get("email_domains") or "")))
    for key, value in expected.items():
        # ИНН и ОГРНИП сверяются и в преамбуле: там их тоже часто указывают.
        with_preamble = key in ("inn", "ogrnip")
        text = region_text + ("\n" + preamble_window if with_preamble else "")
        original = region_original + ("\n" + preamble_original if with_preamble else "")
        if not text.strip():
            continue
        report.checks.append(_compare(key, value, text, original, domains))
    mismatched = [check.label.lower() for check in report.checks if check.status == "mismatch"]
    if mismatched:
        report.warnings.append(
            "Реквизиты поставщика в договоре расходятся с карточкой организации: "
            + ", ".join(mismatched)
            + "."
        )
    return report


def check_info(db: Session, check: Check) -> CheckInfo:
    """Сведения о проверке для запроса к модели; наша сторона — по карточке организации."""
    side = check_side(check.blocks or [], get_organization(db))
    return CheckInfo(
        procurement=check.procurement,
        comment=check.comment,
        ocr_used=check.ocr_used,
        page_count=check.page_count,
        warnings=list(check.warnings or []),
        side_note=side.prompt_note(),
        our_role=side.role if side.status == "found" else None,
    )
