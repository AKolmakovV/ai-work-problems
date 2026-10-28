"""Загрузка и структурирование каталогов «Возможности AI».

Модуль читает Markdown-файлы каталога и превращает их в модели, которые
используются MCP-сервером. Внешних зависимостей нет.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote, unquote

CATALOG_DIRNAME = "Возможности AI"
OVERVIEW_FILENAME = "Описание.md"
RISKS_FILENAME = "Матрица рисков.md"
URI_PREFIX = "ai-capabilities://"

# Стабильные идентификаторы для известных каталогов. Для новых папок
# идентификатор будет построен транслитерацией имени.
KNOWN_CATEGORY_IDS = {
    "Автоматизация действий и процессов": "automation",
    "Аналитика данных": "data-analytics",
    "Консультации, обучение и поиск решений": "consulting",
    "Обработка текстовой информации": "text-processing",
    "Проверка и контроль": "verification",
    "Работа с медиа": "media",
    "Работа с файлами": "files",
    "Разработка и работа с кодом": "development",
}

LEVELS = {
    "низкий": "low",
    "средний": "medium",
    "высокий": "high",
    "критический": "critical",
}
LEVEL_BY_ID = {value: key for key, value in LEVELS.items()}
LEVEL_ALIASES = {
    "низкий": "low",
    "средний": "medium",
    "высокий": "high",
    "критический": "critical",
    "низкая": "low",
    "средняя": "medium",
    "высокая": "high",
    "критическая": "critical",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}

TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


class CatalogError(Exception):
    """Ошибка чтения или поиска в каталоге."""


@dataclass(frozen=True)
class Risk:
    capability_id: str
    capability_title: str
    number: int
    title: str
    risk_category: str
    probability: int
    impact: int
    level: str
    score: int
    mitigations: str
    source: str

    @property
    def id(self) -> str:
        return f"{self.capability_id}-{self.number}"

    @property
    def level_id(self) -> str:
        return LEVELS.get(self.level.casefold(), "unknown")

    @property
    def uri(self) -> str:
        return f"{URI_PREFIX}categories/{quote(self.capability_id)}/risks/{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "risk_category": self.risk_category,
            "probability": self.probability,
            "impact": self.impact,
            "level": self.level,
            "level_id": self.level_id,
            "score": self.score,
            "mitigations": self.mitigations,
            "capability_id": self.capability_id,
            "capability_title": self.capability_title,
            "uri": self.uri,
            "source": self.source,
        }


@dataclass
class Capability:
    id: str
    title: str
    goal: str
    description: str
    bullets: list[str]
    examples: list[str]
    risks: list[Risk]
    source: str
    risks_source: str | None

    @property
    def description_uri(self) -> str:
        return f"{URI_PREFIX}categories/{quote(self.id)}/description"

    @property
    def risks_uri(self) -> str:
        return f"{URI_PREFIX}categories/{quote(self.id)}/risks"

    @property
    def uri(self) -> str:
        return f"{URI_PREFIX}categories/{quote(self.id)}"

    def risk_summary(self) -> dict[str, Any]:
        by_level = {key: 0 for key in LEVEL_BY_ID}
        for risk in self.risks:
            by_level[risk.level_id] = by_level.get(risk.level_id, 0) + 1
        return {
            "risk_count": len(self.risks),
            "critical_risk_count": by_level.get("critical", 0),
            "risks_by_level": by_level,
            "max_risk_score": max((risk.score for risk in self.risks), default=0),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            **self.risk_summary(),
            "description_uri": self.description_uri,
            "risks_uri": self.risks_uri,
            "uri": self.uri,
        }

    def to_dict(self, include_risks: bool = True) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "description": self.description,
            "bullets": self.bullets,
            "examples": self.examples,
            "risk_summary": self.risk_summary(),
            "risks": [risk.to_dict() for risk in self.risks] if include_risks else [],
            "description_uri": self.description_uri,
            "risks_uri": self.risks_uri,
            "uri": self.uri,
            "source": self.source,
            "risks_source": self.risks_source,
        }


@dataclass(frozen=True)
class RiskScale:
    summary: str
    probability: list[dict[str, Any]]
    impact: list[dict[str, Any]]
    levels: list[dict[str, Any]]
    note: str
    markdown: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "probability": self.probability,
            "impact": self.impact,
            "levels": self.levels,
            "note": self.note,
        }


@dataclass
class MarkdownTable:
    header: list[str]
    rows: list[list[str]]
    start_line: int


# ---------------------------------------------------------------------------
# Разбор Markdown
# ---------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    """Приводит текст к виду, удобному для поиска."""
    value = value.casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", value).strip()


def tokenize(value: str) -> list[str]:
    """Разбивает запрос на слова, отбрасывая пунктуацию по краям."""
    return [
        token
        for token in re.split(r"[^\w-]+", normalize_text(value))
        if token
    ]


def strip_inline_markdown(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", value).strip()


def clean_lookup(value: str) -> str:
    """Убирает кавычки и обрамляющие пробелы из пользовательского ввода."""
    return value.strip().strip("`'\"«»").strip()


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    cells: list[str] = []
    buffer: list[str] = []
    escaped = False
    for char in inner:
        if escaped:
            buffer.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    cells.append("".join(buffer).strip())
    return cells


def _is_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in cells)


def iter_tables(markdown: str) -> Iterator[MarkdownTable]:
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        if (
            lines[index].strip().startswith("|")
            and index + 1 < len(lines)
            and _is_separator_row(lines[index + 1])
        ):
            header = _split_table_row(lines[index]) or []
            rows: list[list[str]] = []
            cursor = index + 2
            while cursor < len(lines):
                if not lines[cursor].strip().startswith("|"):
                    break
                row = _split_table_row(lines[cursor])
                if row is not None:
                    rows.append(row)
                cursor += 1
            yield MarkdownTable(header=header, rows=rows, start_line=index + 1)
            index = cursor
        else:
            index += 1


def _first_table(markdown: str) -> MarkdownTable | None:
    return next(iter_tables(markdown), None)


def _h3_sections(markdown: str) -> Iterator[tuple[str, str]]:
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", markdown, re.MULTILINE))
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(markdown)
        yield match.group(1).strip(), markdown[match.end() : end]


def _header_index(
    headers: list[str],
    *needles: str,
    fallback: int | None = None,
) -> int | None:
    normalized = [normalize_text(header) for header in headers]
    for index, header in enumerate(normalized):
        if any(needle in header for needle in needles):
            return index
    return fallback


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _parse_int(value: str) -> int:
    match = re.search(r"\d+", strip_inline_markdown(value))
    if not match:
        raise ValueError(f"не число: {value!r}")
    return int(match.group())


def level_for_score(score: int) -> str:
    if score <= 4:
        return "Низкий"
    if score <= 9:
        return "Средний"
    if score <= 14:
        return "Высокий"
    return "Критический"


def normalize_level(value: str) -> str:
    normalized = normalize_text(value)
    try:
        return LEVEL_ALIASES[normalized]
    except KeyError as exc:
        raise CatalogError(
            "Неизвестный уровень риска: "
            f"{value!r}. Допустимо: низкий, средний, высокий, критический "
            "(или low, medium, high, critical)."
        ) from exc


def slugify(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    chars: list[str] = []
    for char in value:
        if char in TRANSLIT:
            chars.append(TRANSLIT[char])
        elif char.isascii() and char.isalnum():
            chars.append(char)
        else:
            chars.append("-")
    slug = re.sub(r"-+", "-", "".join(chars)).strip("-")
    return slug or "category"


def parse_description(markdown: str) -> tuple[str, list[str], list[str]]:
    goal = ""
    bullets: list[str] = []
    examples: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        goal_match = re.match(r"\*\*Цель:\*\*\s*(.+)", stripped)
        if goal_match and not goal:
            goal = strip_inline_markdown(goal_match.group(1))
            continue
        examples_match = re.match(r"\*\*Примеры:\*\*\s*(.+)", stripped)
        if examples_match:
            examples = [
                strip_inline_markdown(item)
                for item in re.findall(r"«([^»]+)»", examples_match.group(1))
            ]
            continue
        if stripped.startswith("- "):
            bullets.append(strip_inline_markdown(stripped[2:]))
    return goal, bullets, examples


def parse_risks(
    markdown: str,
    capability_id: str,
    capability_title: str,
    source: str,
) -> list[Risk]:
    table: MarkdownTable | None = None
    for candidate in iter_tables(markdown):
        headers = [normalize_text(header) for header in candidate.header]
        if "риск" in headers and any("вероятность" in header for header in headers):
            table = candidate
            break
    if table is None:
        return []

    headers = table.header
    number_index = _header_index(headers, "№", "#", "номер", fallback=0)
    title_index = next(
        (index for index, header in enumerate(headers) if normalize_text(header) == "риск"),
        1,
    )
    category_index = next(
        (
            index
            for index, header in enumerate(headers)
            if normalize_text(header) == "категория"
        ),
        2,
    )
    probability_index = _header_index(headers, "вероятность", fallback=3)
    impact_index = _header_index(headers, "влияние", fallback=4)
    level_index = _header_index(headers, "уровень", fallback=5)
    mitigations_index = _header_index(headers, "меры", fallback=6)

    risks: list[Risk] = []
    for row in table.rows:
        try:
            number = _parse_int(_cell(row, number_index))
            probability = _parse_int(_cell(row, probability_index))
            impact = _parse_int(_cell(row, impact_index))
        except ValueError:
            continue
        if not (1 <= probability <= 5 and 1 <= impact <= 5):
            continue
        level_cell = strip_inline_markdown(_cell(row, level_index))
        score_match = re.search(r"\((\d+)\)", level_cell)
        score = int(score_match.group(1)) if score_match else probability * impact
        if score != probability * impact:
            score = probability * impact
        if not 1 <= score <= 25:
            continue
        title = strip_inline_markdown(_cell(row, title_index))
        if not title:
            continue
        risks.append(
            Risk(
                capability_id=capability_id,
                capability_title=capability_title,
                number=number,
                title=title,
                risk_category=strip_inline_markdown(_cell(row, category_index)),
                probability=probability,
                impact=impact,
                level=level_for_score(score),
                score=score,
                mitigations=strip_inline_markdown(_cell(row, mitigations_index)),
                source=source,
            )
        )
    return risks


def parse_risk_scale(markdown: str) -> RiskScale:
    summary = ""
    for line in markdown.splitlines():
        if "уровень риска =" in line.casefold():
            summary = strip_inline_markdown(line)
            break

    note = ""
    note_match = re.search(r"^\*\*Важно:\*\*\s*(.+)$", markdown, re.MULTILINE)
    if note_match:
        note = strip_inline_markdown(note_match.group(1))

    probability: list[dict[str, Any]] = []
    impact: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []

    for heading, body in _h3_sections(markdown):
        normalized_heading = normalize_text(heading)
        table = _first_table(body)
        if table is None:
            continue

        if "вероятность" in normalized_heading:
            score_index = _header_index(table.header, "оценка", fallback=0)
            level_index = _header_index(table.header, "уровень", fallback=1)
            meaning_index = _header_index(table.header, "означает", fallback=2)
            reference_index = _header_index(table.header, "ориентир", fallback=3)
            probability = [
                {
                    "score": _parse_int_or_none(_cell(row, score_index)),
                    "level": strip_inline_markdown(_cell(row, level_index)),
                    "meaning": strip_inline_markdown(_cell(row, meaning_index)),
                    "reference": strip_inline_markdown(_cell(row, reference_index)),
                }
                for row in table.rows
            ]
        elif "влияние" in normalized_heading:
            score_index = _header_index(table.header, "оценка", fallback=0)
            level_index = _header_index(table.header, "уровень", fallback=1)
            meaning_index = _header_index(table.header, "означает", fallback=2)
            examples_index = _header_index(
                table.header, "примеры", "последстви", fallback=3
            )
            impact = [
                {
                    "score": _parse_int_or_none(_cell(row, score_index)),
                    "level": strip_inline_markdown(_cell(row, level_index)),
                    "meaning": strip_inline_markdown(_cell(row, meaning_index)),
                    "examples": strip_inline_markdown(_cell(row, examples_index)),
                }
                for row in table.rows
            ]
        elif "уровень риска" in normalized_heading:
            name_index = _header_index(table.header, "уровень", fallback=0)
            range_index = _header_index(table.header, "вероятность", "×", fallback=1)
            meaning_index = _header_index(table.header, "означает", fallback=2)
            actions_index = _header_index(table.header, "делать", fallback=3)
            levels = [
                {
                    "level": strip_inline_markdown(_cell(row, name_index)),
                    "range": strip_inline_markdown(_cell(row, range_index)),
                    "meaning": strip_inline_markdown(_cell(row, meaning_index)),
                    "actions": strip_inline_markdown(_cell(row, actions_index)),
                }
                for row in table.rows
            ]

    return RiskScale(
        summary=summary,
        probability=probability,
        impact=impact,
        levels=levels,
        note=note,
        markdown=markdown,
    )


def _parse_int_or_none(value: str) -> int | None:
    try:
        return _parse_int(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Каталог
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class Catalog:
    """Каталог возможностей AI, прочитанный с диска."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise CatalogError(f"Каталог не найден: {self.root}")

        overview_path = self.root / OVERVIEW_FILENAME
        if not overview_path.is_file():
            raise CatalogError(
                f"В каталоге нет файла {OVERVIEW_FILENAME!r}: {self.root}"
            )
        self.overview_path = overview_path
        self.overview = _read_text(overview_path)
        self.scale = parse_risk_scale(self.overview)

        self.capabilities: list[Capability] = []
        used_ids: set[str] = set()
        for directory in sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.name.casefold(),
        ):
            description_path = directory / OVERVIEW_FILENAME
            risks_path = directory / RISKS_FILENAME
            if not description_path.is_file() and not risks_path.is_file():
                continue

            title = directory.name
            category_id = KNOWN_CATEGORY_IDS.get(title) or slugify(title)
            if category_id in used_ids:
                suffix = 2
                while f"{category_id}-{suffix}" in used_ids:
                    suffix += 1
                category_id = f"{category_id}-{suffix}"
            used_ids.add(category_id)

            description = _read_text(description_path) if description_path.is_file() else ""
            goal, bullets, examples = parse_description(description)
            risks: list[Risk] = []
            if risks_path.is_file():
                risks = parse_risks(
                    _read_text(risks_path),
                    capability_id=category_id,
                    capability_title=title,
                    source=risks_path.relative_to(self.root).as_posix(),
                )

            self.capabilities.append(
                Capability(
                    id=category_id,
                    title=title,
                    goal=goal,
                    description=description,
                    bullets=bullets,
                    examples=examples,
                    risks=risks,
                    source=description_path.relative_to(self.root).as_posix()
                    if description_path.is_file()
                    else "",
                    risks_source=risks_path.relative_to(self.root).as_posix()
                    if risks_path.is_file()
                    else None,
                )
            )

        if not self.capabilities:
            raise CatalogError(
                f"В каталоге не найдено ни одной папки с {OVERVIEW_FILENAME!r} "
                f"или {RISKS_FILENAME!r}: {self.root}"
            )

        self._by_id = {capability.id: capability for capability in self.capabilities}
        self._by_title = {
            normalize_text(capability.title): capability
            for capability in self.capabilities
        }
        self._risks_by_id = {
            risk.id.casefold(): risk
            for capability in self.capabilities
            for risk in capability.risks
        }

    @property
    def risks(self) -> list[Risk]:
        return [risk for capability in self.capabilities for risk in capability.risks]

    def stats(self) -> dict[str, Any]:
        by_level = {key: 0 for key in LEVEL_BY_ID}
        for risk in self.risks:
            by_level[risk.level_id] = by_level.get(risk.level_id, 0) + 1
        risk_categories = sorted(
            {risk.risk_category for risk in self.risks if risk.risk_category}
        )
        return {
            "capabilities": len(self.capabilities),
            "risks": len(self.risks),
            "risks_by_level": by_level,
            "risk_categories": risk_categories,
        }

    def get_capability(self, value: str) -> Capability:
        if not isinstance(value, str) or not value.strip():
            raise CatalogError("Укажите идентификатор или название каталога.")
        value = clean_lookup(value)
        normalized = normalize_text(value)
        if normalized in {normalize_text(key) for key in self._by_id}:
            return self._by_id[
                next(key for key in self._by_id if normalize_text(key) == normalized)
            ]
        if normalized in self._by_title:
            return self._by_title[normalized]

        matches = [
            capability
            for capability in self.capabilities
            if normalized in normalize_text(capability.title)
            or normalize_text(capability.title) in normalized
            or normalized == slugify(capability.title)
            or slugify(value) == capability.id
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            options = ", ".join(f"{item.id} ({item.title})" for item in matches)
            raise CatalogError(
                f"Название {value!r} неоднозначно. Подходящие каталоги: {options}."
            )
        options = ", ".join(f"{item.id} ({item.title})" for item in self.capabilities)
        raise CatalogError(
            f"Каталог не найден: {value!r}. Доступные каталоги: {options}."
        )

    def get_risk(self, risk_id: str) -> Risk:
        if not isinstance(risk_id, str) or not risk_id.strip():
            raise CatalogError(
                "Укажите идентификатор риска, например development-1."
            )
        risk_id = clean_lookup(risk_id)
        normalized = risk_id.casefold()
        if normalized in self._risks_by_id:
            return self._risks_by_id[normalized]

        category_part: str | None = None
        number_part: str | None = None
        if ":" in risk_id:
            category_part, number_part = risk_id.rsplit(":", 1)
        elif "#" in risk_id:
            category_part, number_part = risk_id.rsplit("#", 1)
        else:
            match = re.fullmatch(r"(.+)-(\d+)", risk_id.strip())
            if match:
                category_part, number_part = match.groups()

        if category_part and number_part and number_part.isdigit():
            capability = self.get_capability(category_part)
            number = int(number_part)
            for risk in capability.risks:
                if risk.number == number:
                    return risk

        raise CatalogError(
            f"Риск не найден: {risk_id!r}. Используйте идентификатор вида "
            "development-1 или список list_risks."
        )

    def filter_risks(
        self,
        category: str | None = None,
        level: str | None = None,
        risk_category: str | None = None,
        min_probability: int | None = None,
        min_impact: int | None = None,
        min_score: int | None = None,
        query: str | None = None,
        sort_by: str = "id",
    ) -> list[Risk]:
        risks = self.risks

        if category:
            capability = self.get_capability(category)
            risks = [risk for risk in risks if risk.capability_id == capability.id]
        if level:
            level_id = normalize_level(level)
            risks = [risk for risk in risks if risk.level_id == level_id]
        if risk_category:
            normalized_category = normalize_text(risk_category)
            risks = [
                risk
                for risk in risks
                if normalized_category in normalize_text(risk.risk_category)
            ]
        if min_probability is not None:
            risks = [risk for risk in risks if risk.probability >= min_probability]
        if min_impact is not None:
            risks = [risk for risk in risks if risk.impact >= min_impact]
        if min_score is not None:
            risks = [risk for risk in risks if risk.score >= min_score]
        if query:
            terms = tokenize(query)
            if terms:
                risks = [
                    risk
                    for risk in risks
                    if all(
                        term
                        in normalize_text(
                            " ".join(
                                [
                                    risk.title,
                                    risk.risk_category,
                                    risk.mitigations,
                                    risk.capability_title,
                                ]
                            )
                        )
                        for term in terms
                    )
                ]

        if sort_by == "score":
            risks.sort(key=lambda risk: (-risk.score, risk.capability_title, risk.number))
        elif sort_by == "probability":
            risks.sort(
                key=lambda risk: (
                    -risk.probability,
                    -risk.impact,
                    risk.capability_title,
                    risk.number,
                )
            )
        elif sort_by == "impact":
            risks.sort(
                key=lambda risk: (
                    -risk.impact,
                    -risk.probability,
                    risk.capability_title,
                    risk.number,
                )
            )
        else:
            risks.sort(key=lambda risk: (risk.capability_title, risk.number))
        return risks

    def search(
        self,
        query: str,
        category: str | None = None,
        include_capabilities: bool = True,
        include_risks: bool = True,
        limit: int | None = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = normalize_text(query)
        terms = tokenize(query)
        if not terms:
            raise CatalogError("Поисковый запрос не должен быть пустым.")

        capabilities = self.capabilities
        if category:
            capabilities = [self.get_capability(category)]

        hits: list[dict[str, Any]] = []
        for capability in capabilities:
            if include_capabilities:
                score, matched = _score_fields(
                    [
                        (capability.title, 6),
                        (capability.goal, 3),
                        (capability.description, 1),
                    ],
                    terms,
                    normalized_query,
                )
                if score:
                    snippet, field = _best_snippet(
                        [
                            (capability.goal, 3, "goal"),
                            (capability.description, 2, "description"),
                            (capability.title, 1, "title"),
                        ],
                        terms,
                    )
                    hits.append(
                        {
                            "type": "capability",
                            "id": capability.id,
                            "title": capability.title,
                            "context": capability.goal,
                            "field": field,
                            "snippet": snippet,
                            "score": score,
                            "matched_terms": sorted(matched),
                            "uri": capability.uri,
                        }
                    )

            if include_risks:
                for risk in capability.risks:
                    score, matched = _score_fields(
                        [
                            (risk.title, 6),
                            (risk.risk_category, 2),
                            (risk.mitigations, 1),
                            (risk.capability_title, 2),
                        ],
                        terms,
                        normalized_query,
                    )
                    if not score:
                        continue
                    snippet, field = _best_snippet(
                        [
                            (risk.mitigations, 4, "mitigations"),
                            (risk.risk_category, 2, "risk_category"),
                            (risk.title, 1, "title"),
                        ],
                        terms,
                    )
                    hits.append(
                        {
                            "type": "risk",
                            "id": risk.id,
                            "title": risk.title,
                            "context": (
                                f"{risk.capability_title} · "
                                f"{risk.risk_category} · {risk.level} ({risk.score})"
                            ),
                            "field": field,
                            "snippet": snippet,
                            "score": score,
                            "matched_terms": sorted(matched),
                            "uri": risk.uri,
                        }
                    )

        hits.sort(
            key=lambda hit: (
                -hit["score"],
                hit["type"] != "capability",
                hit["id"],
            )
        )
        return hits if limit is None else hits[:limit]

    def index_resource(self) -> dict[str, Any]:
        return {
            "name": "Возможности AI",
            "description": (
                "Каталог возможностей применения нейросетей и матриц рисков."
            ),
            "stats": self.stats(),
            "capabilities": [capability.summary() for capability in self.capabilities],
            "risk_categories": self.stats()["risk_categories"],
            "levels": [
                {"id": level_id, "title": title.capitalize()}
                for title, level_id in LEVELS.items()
            ],
            "resources": {
                "overview": f"{URI_PREFIX}overview",
                "risk_scale": f"{URI_PREFIX}risk-scale",
                "index": f"{URI_PREFIX}index",
            },
        }

    def resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = [
            self._resource_entry(
                uri=f"{URI_PREFIX}overview",
                name="overview",
                title="Возможности AI: обзор и шкала рисков",
                description=(
                    "Главный документ каталога: назначение, принцип категоризации "
                    "и полная шкала оценки рисков."
                ),
                path=self.overview_path,
                priority=1.0,
            ),
            self._resource_entry(
                uri=f"{URI_PREFIX}risk-scale",
                name="risk-scale",
                title="Шкала оценки рисков",
                description=(
                    "Вероятность, влияние, уровни риска и правила их расчёта."
                ),
                path=self.overview_path,
                priority=0.9,
            ),
            {
                "uri": f"{URI_PREFIX}index",
                "name": "index",
                "title": "Индекс каталога",
                "description": (
                    "Машиночитаемый индекс: категории, количество рисков и URI."
                ),
                "mimeType": "application/json",
                "annotations": {"audience": ["assistant"], "priority": 0.8},
            },
        ]
        for capability in self.capabilities:
            if capability.source:
                resources.append(
                    self._resource_entry(
                        uri=capability.description_uri,
                        name=f"{capability.id}-description",
                        title=f"{capability.title}: описание",
                        description=f"Цель, задачи и примеры: {capability.title}.",
                        path=self.root / capability.source,
                        priority=0.7,
                    )
                )
            if capability.risks_source:
                resources.append(
                    self._resource_entry(
                        uri=capability.risks_uri,
                        name=f"{capability.id}-risks",
                        title=f"{capability.title}: матрица рисков",
                        description=(
                            f"Матрица рисков для категории «{capability.title}»."
                        ),
                        path=self.root / capability.risks_source,
                        priority=0.8,
                    )
                )
        return resources

    def resource_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "uriTemplate": f"{URI_PREFIX}categories/{{category}}/description",
                "name": "category-description",
                "title": "Описание каталога возможностей",
                "description": (
                    "Markdown-описание категории. {category} — идентификатор, "
                    "например development или data-analytics."
                ),
                "mimeType": "text/markdown",
            },
            {
                "uriTemplate": f"{URI_PREFIX}categories/{{category}}/risks",
                "name": "category-risks",
                "title": "Матрица рисков категории",
                "description": (
                    "Полная матрица рисков категории в формате Markdown."
                ),
                "mimeType": "text/markdown",
            },
            {
                "uriTemplate": f"{URI_PREFIX}categories/{{category}}",
                "name": "category",
                "title": "Каталог возможностей и риски",
                "description": "Описание категории и её матрица рисков одним документом.",
                "mimeType": "text/markdown",
            },
            {
                "uriTemplate": (
                    f"{URI_PREFIX}categories/{{category}}/risks/{{number}}"
                ),
                "name": "risk",
                "title": "Карточка риска",
                "description": (
                    "Один риск с оценками и мерами снижения. {number} — номер "
                    "риска в матрице категории."
                ),
                "mimeType": "text/markdown",
            },
        ]

    def read_resource(self, uri: str) -> tuple[str, str]:
        """Возвращает (mimeType, text) для URI ресурса."""
        if uri != URI_PREFIX:
            uri = uri.rstrip("/")
        if uri == f"{URI_PREFIX}overview":
            return "text/markdown", self.overview
        if uri == f"{URI_PREFIX}risk-scale":
            match = re.search(r"^##\s+Шкала оценки рисков\s*$", self.overview, re.MULTILINE)
            text = self.overview[match.start() :] if match else self.overview
            return "text/markdown", text
        if uri == f"{URI_PREFIX}index":
            return (
                "application/json",
                json.dumps(self.index_resource(), ensure_ascii=False, indent=2),
            )

        if not uri.startswith(URI_PREFIX):
            raise CatalogError(f"Неизвестный ресурс: {uri}")
        parts = [
            unquote(part)
            for part in uri[len(URI_PREFIX) :].split("/")
            if part
        ]
        if len(parts) < 2 or parts[0] != "categories":
            raise CatalogError(f"Неизвестный ресурс: {uri}")

        capability = self.get_capability(parts[1])
        if len(parts) == 2:
            matrix = ""
            if capability.risks_source:
                matrix = _read_text(self.root / capability.risks_source)
            else:
                matrix = "Матрица рисков для этой категории не найдена."
            text = (
                f"# {capability.title}\n\n"
                f"{capability.description.strip()}\n\n"
                f"---\n\n{matrix.strip()}\n"
            )
            return "text/markdown", text

        section = parts[2].casefold()
        if section == "description":
            if not capability.source:
                raise CatalogError(
                    f"Описание для категории «{capability.title}» не найдено."
                )
            return "text/markdown", _read_text(self.root / capability.source)
        if section == "risks":
            if len(parts) == 3:
                if not capability.risks_source:
                    raise CatalogError(
                        f"Матрица рисков для категории «{capability.title}» не найдена."
                    )
                return "text/markdown", _read_text(self.root / capability.risks_source)
            if len(parts) == 4 and parts[3].isdigit():
                risk = next(
                    (
                        item
                        for item in capability.risks
                        if item.number == int(parts[3])
                    ),
                    None,
                )
                if risk is None:
                    raise CatalogError(
                        f"Риск №{parts[3]} не найден в категории «{capability.title}»."
                    )
                return "text/markdown", format_risk_markdown(risk)
        raise CatalogError(f"Неизвестный ресурс: {uri}")

    @staticmethod
    def _resource_entry(
        uri: str,
        name: str,
        title: str,
        description: str,
        path: Path,
        priority: float,
    ) -> dict[str, Any]:
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        entry: dict[str, Any] = {
            "uri": uri,
            "name": name,
            "title": title,
            "description": description,
            "mimeType": "text/markdown",
            "annotations": {"audience": ["assistant", "user"], "priority": priority},
        }
        if size is not None:
            entry["size"] = size
        return entry


# ---------------------------------------------------------------------------
# Поиск и форматирование
# ---------------------------------------------------------------------------


def _score_fields(
    fields: Iterable[tuple[str, int]],
    terms: list[str],
    phrase: str,
) -> tuple[int, set[str]]:
    score = 0
    matched: set[str] = set()
    for text, weight in fields:
        normalized = normalize_text(text)
        for term in terms:
            if term in normalized:
                score += weight
                matched.add(term)
        if phrase and len(terms) > 1 and phrase in normalized:
            score += weight * 2
    return score, matched


def _best_snippet(
    fields: Iterable[tuple[str, int, str]],
    terms: list[str],
    width: int = 220,
) -> tuple[str, str]:
    best: tuple[int, str, str] | None = None
    for text, weight, label in fields:
        haystack = text.casefold().replace("ё", "е")
        positions = [haystack.find(term) for term in terms if term in haystack]
        if not positions:
            continue
        position = min(positions)
        start = max(0, position - width // 3)
        if start > 0:
            boundary = text.rfind(" ", 0, start + 1)
            if boundary != -1:
                start = boundary + 1
        end = min(len(text), start + width)
        if end < len(text):
            boundary = text.find(" ", end)
            if boundary != -1:
                end = boundary
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        candidate = (weight, snippet, label)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        text, _, label = next(iter(fields))
        snippet = re.sub(r"\s+", " ", text)[:width].strip()
        return snippet, label
    return best[1], best[2]


def md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def format_risk_markdown(risk: Risk) -> str:
    return (
        f"# Риск {risk.id}: {risk.title}\n\n"
        f"- **Категория возможности:** {risk.capability_title}\n"
        f"- **Категория риска:** {risk.risk_category}\n"
        f"- **Вероятность:** {risk.probability}\n"
        f"- **Влияние:** {risk.impact}\n"
        f"- **Уровень:** {risk.level} ({risk.score})\n"
        f"- **Источник:** {risk.source}\n\n"
        f"## Меры снижения\n\n{risk.mitigations}\n"
    )
