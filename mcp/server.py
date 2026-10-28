#!/usr/bin/env python3
"""MCP-сервер каталога «Возможности AI».

Сервер работает по stdio, не требует внешних зависимостей и предоставляет
доступ только для чтения к Markdown-каталогам:

* инструменты для списка возможностей, рисков, поиска и шкалы оценок;
* ресурсы с исходными Markdown-документами;
* готовые промпты для анализа сценариев.

Запуск:

    python3 mcp/server.py

Проверка каталога:

    python3 mcp/server.py --check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from catalog import (
    CATALOG_DIRNAME,
    URI_PREFIX,
    Catalog,
    CatalogError,
    format_risk_markdown,
    md_cell,
    normalize_text,
)

SERVER_NAME = "ai-capabilities"
SERVER_VERSION = "1.0.0"
LATEST_MODERN_VERSION = "2026-07-28"
LATEST_LEGACY_VERSION = "2025-11-25"
SUPPORTED_VERSIONS = [
    LATEST_MODERN_VERSION,
    LATEST_LEGACY_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]
LEGACY_VERSIONS = set(SUPPORTED_VERSIONS[1:])

SERVER_INFO = {
    "name": SERVER_NAME,
    "title": "Каталог «Возможности AI»",
    "version": SERVER_VERSION,
}

CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"listChanged": False, "subscribe": False},
    "prompts": {"listChanged": False},
}

INSTRUCTIONS = (
    "Сервер предоставляет каталог возможностей применения AI и связанные с ними "
    "матрицы рисков (папка «Возможности AI»).\n\n"
    "Порядок работы:\n"
    "1. list_capabilities — получите список категорий и сводку рисков.\n"
    "2. get_capability — изучите описание выбранной категории.\n"
    "3. list_risks или search_catalog — найдите релевантные риски.\n"
    "4. get_risk — получите полную карточку риска с мерами снижения.\n"
    "5. get_risk_scale — уточните шкалу вероятности, влияния и уровней.\n\n"
    "Все данные доступны только для чтения. Идентификаторы рисков имеют вид "
    "development-1, files-2 и т. п. Не придумывайте риски и идентификаторы: "
    "используйте только данные сервера."
)

TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_capabilities",
        "title": "Список возможностей AI",
        "description": (
            "Возвращает все категории каталога «Возможности AI»: цель, "
            "количество рисков, число критических рисков и максимальный балл."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Список возможностей AI"},
    },
    {
        "name": "get_capability",
        "title": "Описание категории",
        "description": (
            "Возвращает описание категории возможностей: цель, задачи, примеры "
            "и (по умолчанию) полную матрицу рисков."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Идентификатор или название категории, например "
                        "development, data-analytics или «Работа с файлами»."
                    ),
                },
                "include_risks": {
                    "type": "boolean",
                    "description": "Включать полный список рисков. По умолчанию true.",
                    "default": True,
                },
            },
            "required": ["category"],
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Описание категории"},
    },
    {
        "name": "list_risks",
        "title": "Список рисков",
        "description": (
            "Возвращает риски с фильтрами по категории возможностей, уровню, "
            "категории риска, вероятности, влиянию и тексту. Поддерживает "
            "сортировку и постраничный вывод."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Идентификатор или название категории возможностей.",
                },
                "level": {
                    "type": "string",
                    "description": (
                        "Уровень риска: низкий, средний, высокий, критический "
                        "(или low, medium, high, critical)."
                    ),
                },
                "risk_category": {
                    "type": "string",
                    "description": (
                        "Категория риска, например «Безопасность», «Данные и "
                        "конфиденциальность», «Правовые и комплаенс»."
                    ),
                },
                "min_probability": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Минимальная вероятность (1–5).",
                },
                "min_impact": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Минимальное влияние (1–5).",
                },
                "min_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Минимальный балл риска (вероятность × влияние).",
                },
                "query": {
                    "type": "string",
                    "description": "Слова для поиска по названию и мерам снижения.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["id", "score", "probability", "impact"],
                    "default": "id",
                    "description": "Сортировка результатов.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                    "description": "Максимальное число рисков в ответе.",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 0,
                    "description": "Смещение для постраничного вывода.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Список рисков"},
    },
    {
        "name": "get_risk",
        "title": "Карточка риска",
        "description": (
            "Возвращает один риск по идентификатору: оценки, уровень, категорию "
            "и меры снижения."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "risk_id": {
                    "type": "string",
                    "description": (
                        "Идентификатор риска: development-1, files-2, "
                        "data-analytics:3 и т. п."
                    ),
                }
            },
            "required": ["risk_id"],
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Карточка риска"},
    },
    {
        "name": "search_catalog",
        "title": "Поиск по каталогу",
        "description": (
            "Полнотекстовый поиск по описаниям возможностей, названиям рисков "
            "и мерам снижения. Возвращает фрагменты и идентификаторы для "
            "последующего get_capability или get_risk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос, например «утечка данных».",
                },
                "category": {
                    "type": "string",
                    "description": "Ограничить поиск одной категорией возможностей.",
                },
                "include_capabilities": {
                    "type": "boolean",
                    "default": True,
                    "description": "Искать среди описаний категорий.",
                },
                "include_risks": {
                    "type": "boolean",
                    "default": True,
                    "description": "Искать среди рисков.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "Максимальное число результатов.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Поиск по каталогу"},
    },
    {
        "name": "get_risk_scale",
        "title": "Шкала оценки рисков",
        "description": (
            "Возвращает единую шкалу: вероятность 1–5, влияние 1–5 и правила "
            "определения уровня риска."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Шкала оценки рисков"},
    },
    {
        "name": "get_overview",
        "title": "Обзор каталога",
        "description": (
            "Возвращает главный документ каталога целиком: принцип категоризации "
            "и шкалу оценки рисков."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {**TOOL_ANNOTATIONS, "title": "Обзор каталога"},
    },
]

PROMPTS: list[dict[str, Any]] = [
    {
        "name": "analyze_scenario",
        "title": "Анализ сценария внедрения AI",
        "description": (
            "По описанию сценария определить применимые возможности и оценить "
            "риски по данным каталога."
        ),
        "arguments": [
            {
                "name": "scenario",
                "description": "Что именно планируется делать с помощью AI.",
                "required": True,
            },
            {
                "name": "constraints",
                "description": "Ограничения, контекст, данные и требования.",
                "required": False,
            },
        ],
    },
    {
        "name": "review_capability_risks",
        "title": "Разбор рисков категории",
        "description": (
            "Проверить матрицу рисков выбранной категории применительно к "
            "конкретному контексту."
        ),
        "arguments": [
            {
                "name": "category",
                "description": "Идентификатор или название категории.",
                "required": True,
            },
            {
                "name": "context",
                "description": "Контекст применения и ограничения.",
                "required": False,
            },
        ],
    },
]


class ProtocolError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class ToolInputError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Валидация аргументов инструментов
# ---------------------------------------------------------------------------


def _string_arg(
    arguments: dict[str, Any],
    name: str,
    *,
    required: bool = False,
    default: str | None = None,
) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise ToolInputError(f"Не указан обязательный параметр {name!r}.")
        return default
    if not isinstance(value, str):
        raise ToolInputError(f"Параметр {name!r} должен быть строкой.")
    value = value.strip()
    if required and not value:
        raise ToolInputError(f"Параметр {name!r} не должен быть пустым.")
    return value or default


def _bool_arg(
    arguments: dict[str, Any], name: str, default: bool
) -> bool:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolInputError(f"Параметр {name!r} должен быть логическим.")
    return value


def _int_arg(
    arguments: dict[str, Any],
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    value = arguments.get(name)
    if value is None:
        return default
    if type(value) is not int:
        raise ToolInputError(f"Параметр {name!r} должен быть целым числом.")
    if minimum is not None and value < minimum:
        raise ToolInputError(f"Параметр {name!r} должен быть не меньше {minimum}.")
    if maximum is not None and value > maximum:
        raise ToolInputError(f"Параметр {name!r} должен быть не больше {maximum}.")
    return value


# ---------------------------------------------------------------------------
# Инструменты
# ---------------------------------------------------------------------------


def tool_list_capabilities(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    capabilities = [capability.summary() for capability in catalog.capabilities]
    data = {
        "count": len(capabilities),
        "stats": catalog.stats(),
        "capabilities": capabilities,
    }
    lines = [
        f"# Каталоги возможностей AI ({len(capabilities)})",
        "",
        "| ID | Категория | Цель | Рисков | Критических | Макс. балл |",
        "|---|---|---|---:|---:|---:|",
    ]
    for capability in capabilities:
        lines.append(
            "| {id} | {title} | {goal} | {risk_count} | {critical} | {max_score} |".format(
                id=md_cell(capability["id"]),
                title=md_cell(capability["title"]),
                goal=md_cell(capability["goal"]),
                risk_count=capability["risk_count"],
                critical=capability["critical_risk_count"],
                max_score=capability["max_risk_score"],
            )
        )
    lines.extend(
        [
            "",
            f"Всего рисков: {data['stats']['risks']}.",
            "Для деталей используйте get_capability или list_risks.",
        ]
    )
    return data, "\n".join(lines)


def tool_get_capability(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    category = _string_arg(arguments, "category", required=True)
    assert category is not None
    include_risks = _bool_arg(arguments, "include_risks", True)
    capability = catalog.get_capability(category)
    data = capability.to_dict(include_risks=include_risks)

    lines = [
        f"# {capability.title}",
        "",
        f"**ID:** `{capability.id}`",
        "",
        capability.description.strip(),
        "",
        "## Сводка рисков",
        "",
        (
            f"Всего: {data['risk_summary']['risk_count']}; "
            f"критических: {data['risk_summary']['critical_risk_count']}; "
            f"максимальный балл: {data['risk_summary']['max_risk_score']}."
        ),
    ]
    if include_risks:
        lines.extend(
            [
                "",
                "## Риски",
                "",
                "| ID | Риск | Категория риска | Вероятность | Влияние | Уровень |",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for risk in capability.risks:
            lines.append(
                f"| {md_cell(risk.id)} | {md_cell(risk.title)} | "
                f"{md_cell(risk.risk_category)} | {risk.probability} | "
                f"{risk.impact} | {md_cell(risk.level)} ({risk.score}) |"
            )
        lines.append("")
        lines.append("Меры снижения доступны в get_risk(risk_id).")
    else:
        lines.append("")
        lines.append("Список рисков не запрошен; используйте list_risks или get_risk.")
    return data, "\n".join(lines)


def tool_list_risks(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    category = _string_arg(arguments, "category")
    level = _string_arg(arguments, "level")
    risk_category = _string_arg(arguments, "risk_category")
    query = _string_arg(arguments, "query")
    sort_by = _string_arg(arguments, "sort_by", default="id") or "id"
    if sort_by not in {"id", "score", "probability", "impact"}:
        raise ToolInputError(
            "Параметр 'sort_by' должен быть одним из: id, score, probability, impact."
        )
    min_probability = _int_arg(
        arguments, "min_probability", minimum=1, maximum=5
    )
    min_impact = _int_arg(arguments, "min_impact", minimum=1, maximum=5)
    min_score = _int_arg(arguments, "min_score", minimum=1, maximum=25)
    limit = _int_arg(arguments, "limit", default=50, minimum=1, maximum=100) or 50
    offset = _int_arg(arguments, "offset", default=0, minimum=0, maximum=1000) or 0

    risks = catalog.filter_risks(
        category=category,
        level=level,
        risk_category=risk_category,
        min_probability=min_probability,
        min_impact=min_impact,
        min_score=min_score,
        query=query,
        sort_by=sort_by,
    )
    page = risks[offset : offset + limit]
    data = {
        "total": len(risks),
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "filters": {
            "category": category,
            "level": level,
            "risk_category": risk_category,
            "min_probability": min_probability,
            "min_impact": min_impact,
            "min_score": min_score,
            "query": query,
            "sort_by": sort_by,
        },
        "risks": [risk.to_dict() for risk in page],
    }

    lines = [
        f"# Риски: найдено {len(risks)}, показано {len(page)}"
        + (f" (смещение {offset})" if offset else ""),
        "",
        "| ID | Риск | Категория | Тип риска | Вероятность | Влияние | Уровень |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for risk in page:
        lines.append(
            f"| {md_cell(risk.id)} | {md_cell(risk.title)} | "
            f"{md_cell(risk.capability_title)} | {md_cell(risk.risk_category)} | "
            f"{risk.probability} | {risk.impact} | "
            f"{md_cell(risk.level)} ({risk.score}) |"
        )
    if not page:
        lines.append("| — | Риски не найдены | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "Полные меры снижения и источник — в get_risk(risk_id).",
        ]
    )
    return data, "\n".join(lines)


def tool_get_risk(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    risk_id = _string_arg(arguments, "risk_id", required=True)
    assert risk_id is not None
    risk = catalog.get_risk(risk_id)
    capability = catalog.get_capability(risk.capability_id)
    data = risk.to_dict()
    data["capability"] = {
        "id": capability.id,
        "title": capability.title,
        "goal": capability.goal,
        "description_uri": capability.description_uri,
        "risks_uri": capability.risks_uri,
    }
    return data, format_risk_markdown(risk)


def tool_search_catalog(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    query = _string_arg(arguments, "query", required=True)
    assert query is not None
    category = _string_arg(arguments, "category")
    include_capabilities = _bool_arg(arguments, "include_capabilities", True)
    include_risks = _bool_arg(arguments, "include_risks", True)
    limit = _int_arg(arguments, "limit", default=10, minimum=1, maximum=50) or 10

    hits_all = catalog.search(
        query,
        category=category,
        include_capabilities=include_capabilities,
        include_risks=include_risks,
        limit=None,
    )
    hits = hits_all[:limit]
    data = {
        "query": query,
        "total": len(hits_all),
        "count": len(hits),
        "hits": hits,
    }
    lines = [
        f"# Поиск: {query}",
        "",
        f"Найдено: {len(hits_all)}; показано: {len(hits)}.",
        "",
    ]
    for index, hit in enumerate(hits, start=1):
        kind = "возможность" if hit["type"] == "capability" else "риск"
        lines.append(
            f"{index}. **{hit['id']}** ({kind}) — {hit['title']}"
        )
        if normalize_text(hit["snippet"]) != normalize_text(hit["title"]):
            lines.append(f"   {hit['snippet']}")
        lines.append(f"   URI: `{hit['uri']}`")
        lines.append("")
    if not hits:
        lines.append("Ничего не найдено. Попробуйте изменить формулировку.")
    return data, "\n".join(lines)


def tool_get_risk_scale(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    scale = catalog.scale
    data = {
        "scale": scale.to_dict(),
        "source_uri": f"{URI_PREFIX}risk-scale",
    }
    lines = ["# Шкала оценки рисков", ""]
    if scale.summary:
        lines.extend([scale.summary, ""])
    lines.extend(
        [
            "## Вероятность",
            "",
            "| Оценка | Уровень | Что означает | Ориентир |",
            "|---:|---|---|---|",
        ]
    )
    for row in scale.probability:
        lines.append(
            f"| {md_cell(row['score'])} | {md_cell(row['level'])} | "
            f"{md_cell(row['meaning'])} | {md_cell(row['reference'])} |"
        )
    lines.extend(
        [
            "",
            "## Влияние",
            "",
            "| Оценка | Уровень | Что означает | Примеры последствий |",
            "|---:|---|---|---|",
        ]
    )
    for row in scale.impact:
        lines.append(
            f"| {md_cell(row['score'])} | {md_cell(row['level'])} | "
            f"{md_cell(row['meaning'])} | {md_cell(row['examples'])} |"
        )
    lines.extend(
        [
            "",
            "## Уровень риска",
            "",
            "| Уровень | Вероятность × Влияние | Что означает | Что делать |",
            "|---|---|---|---|",
        ]
    )
    for row in scale.levels:
        lines.append(
            f"| {md_cell(row['level'])} | {md_cell(row['range'])} | "
            f"{md_cell(row['meaning'])} | {md_cell(row['actions'])} |"
        )
    if scale.note:
        lines.extend(["", f"**Важно:** {scale.note}"])
    return data, "\n".join(lines)


def tool_get_overview(
    arguments: dict[str, Any], catalog: Catalog
) -> tuple[dict[str, Any], str]:
    return (
        {
            "markdown": catalog.overview,
            "source_uri": f"{URI_PREFIX}overview",
        },
        catalog.overview,
    )


TOOL_HANDLERS: dict[
    str, Callable[[dict[str, Any], Catalog], tuple[dict[str, Any], str]]
] = {
    "list_capabilities": tool_list_capabilities,
    "get_capability": tool_get_capability,
    "list_risks": tool_list_risks,
    "get_risk": tool_get_risk,
    "search_catalog": tool_search_catalog,
    "get_risk_scale": tool_get_risk_scale,
    "get_overview": tool_get_overview,
}


# ---------------------------------------------------------------------------
# Промпты и протокол
# ---------------------------------------------------------------------------


def build_prompt(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "analyze_scenario":
        scenario = _string_arg(arguments, "scenario", required=True)
        constraints = _string_arg(arguments, "constraints") or "не заданы"
        assert scenario is not None
        text = f"""Ты анализируешь сценарий внедрения AI с точки зрения возможностей и рисков.

Сценарий:
{scenario}

Ограничения и контекст:
{constraints}

Используй MCP-сервер ai-capabilities:
1. Вызови search_catalog или list_capabilities, чтобы определить применимые категории.
2. Вызови get_capability для выбранных категорий.
3. Вызови list_risks или get_risk, чтобы получить релевантные риски и меры снижения.
4. Вызови get_risk_scale, чтобы корректно интерпретировать уровни риска.

Подготовь ответ:
## Применимые возможности
Категории и почему они подходят.

## Ключевые риски
Для каждого риска: risk_id, уровень (балл), почему он применим в этом сценарии, меры снижения.

## Приоритеты
Что сделать до запуска, что — во время, что — после.

## Вывод
Можно ли внедрять сценарий, при каких условиях и что остаётся неизвестным.

Не придумывай риски и идентификаторы. Используй только данные сервера. Если данных не хватает, явно укажи пробелы."""
        return {
            "description": "Анализ сценария внедрения AI по каталогу возможностей и рисков.",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ],
        }

    if name == "review_capability_risks":
        category = _string_arg(arguments, "category", required=True)
        context = _string_arg(arguments, "context") or "не задан"
        assert category is not None
        text = f"""Проверь матрицу рисков категории «{category}» применительно к указанному контексту.

Контекст:
{context}

Используй MCP-сервер ai-capabilities:
1. Вызови get_capability с category={category!r}.
2. Вызови list_risks с этой же категорией.
3. При необходимости открой отдельные риски через get_risk и уточни шкалу через get_risk_scale.

Для каждого риска определи:
- применим ли он в данном контексте и почему;
- достаточно ли предложенных мер или нужны дополнительные;
- кто должен быть владельцем меры и когда её проверить.

Сформируй таблицу «риск — применимость — меры — владелец — срок проверки» и итоговый список действий. Ссылайся на risk_id из сервера и не добавляй риски, которых нет в каталоге."""
        return {
            "description": "Разбор применимости матрицы рисков для конкретной категории.",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ],
        }

    raise ProtocolError(-32602, f"Unknown prompt: {name}")


def log(message: str) -> None:
    if os.environ.get("AI_CAPABILITIES_MCP_LOG"):
        print(message, file=sys.stderr, flush=True)


def _tool_error(message: str) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def call_tool(
    name: str, arguments: Any, catalog: Catalog
) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return _tool_error("Аргументы инструмента должны быть объектом.")
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ProtocolError(-32602, f"Unknown tool: {name}")
    try:
        data, text = handler(arguments, catalog)
    except (CatalogError, ToolInputError) as error:
        return _tool_error(str(error))
    except Exception as error:  # pragma: no cover - защита от неожиданных ошибок
        log(traceback.format_exc())
        return _tool_error(f"Внутренняя ошибка инструмента: {error}")
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": False,
    }


class MCPServer:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def handle(self, message: Any) -> dict[str, Any] | list[Any] | None:
        if isinstance(message, list):
            responses = [self._handle_one(item) for item in message]
            responses = [item for item in responses if item is not None]
            return responses or None
        return self._handle_one(message)

    def _handle_one(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return self._error(None, -32600, "Invalid Request")
        if not isinstance(message.get("method"), str):
            return self._error(
                message.get("id"), -32600, "Invalid Request: method is required"
            )

        method = message["method"]
        request_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if is_notification:
                return None
            return self._error(request_id, -32602, "Invalid params")

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "server/discover":
                self._check_version(params)
                result = self._discover()
            elif method.startswith("notifications/"):
                return None
            else:
                self._check_version(params)
                result = self._dispatch(method, params)
        except ProtocolError as error:
            if is_notification:
                return None
            return self._error(request_id, error.code, error.message, error.data)
        except Exception as error:  # pragma: no cover - защита протокола
            log(traceback.format_exc())
            if is_notification:
                return None
            return self._error(request_id, -32603, f"Internal error: {error}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if requested in LEGACY_VERSIONS:
            negotiated = requested
        else:
            negotiated = LATEST_LEGACY_VERSION
        return {
            "protocolVersion": negotiated,
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }

    def _discover(self) -> dict[str, Any]:
        return {
            "resultType": "complete",
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": CAPABILITIES,
            "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
            "instructions": INSTRUCTIONS,
        }

    def _check_version(self, params: dict[str, Any]) -> None:
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return
        version = meta.get("io.modelcontextprotocol/protocolVersion")
        if version is not None and version not in SUPPORTED_VERSIONS:
            raise ProtocolError(
                -32022,
                "Unsupported protocol version",
                {"supported": SUPPORTED_VERSIONS, "requested": version},
            )

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "ping":
            return {"resultType": "complete"}

        if method == "tools/list":
            return {"resultType": "complete", "tools": TOOLS}

        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ProtocolError(-32602, "Invalid params: tool name is required")
            result = call_tool(
                name,
                params.get("arguments", {}),
                self.catalog,
            )
            return result

        if method == "resources/list":
            return {
                "resultType": "complete",
                "resources": self.catalog.resources(),
            }

        if method == "resources/templates/list":
            return {
                "resultType": "complete",
                "resourceTemplates": self.catalog.resource_templates(),
            }

        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ProtocolError(-32602, "Invalid params: uri is required")
            try:
                mime_type, text = self.catalog.read_resource(uri)
            except CatalogError as error:
                raise ProtocolError(
                    -32602, "Resource not found", {"uri": uri, "reason": str(error)}
                ) from error
            return {
                "resultType": "complete",
                "contents": [
                    {"uri": uri, "mimeType": mime_type, "text": text}
                ],
            }

        if method == "prompts/list":
            return {"resultType": "complete", "prompts": PROMPTS}

        if method == "prompts/get":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ProtocolError(-32602, "Invalid params: prompt name is required")
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ProtocolError(-32602, "Invalid params: arguments must be an object")
            try:
                return {
                    "resultType": "complete",
                    **build_prompt(name, arguments),
                }
            except (ToolInputError, CatalogError) as error:
                raise ProtocolError(-32602, str(error)) from error

        if method == "logging/setLevel":
            return {"resultType": "complete"}

        raise ProtocolError(-32601, f"Method not found: {method}")

    @staticmethod
    def _error(
        request_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def parse_error() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }


def write_message(stdout: Any, message: Any) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    stdout.write(payload.encode("utf-8") + b"\n")
    stdout.flush()


def serve(catalog: Catalog, stdin: Any = None, stdout: Any = None) -> int:
    input_stream = stdin if stdin is not None else sys.stdin.buffer
    output_stream = stdout if stdout is not None else sys.stdout.buffer
    server = MCPServer(catalog)

    while True:
        try:
            raw = input_stream.readline()
        except KeyboardInterrupt:
            return 0
        if not raw:
            return 0
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            write_message(output_stream, parse_error())
            continue
        try:
            response = server.handle(message)
        except Exception as error:  # pragma: no cover - последний рубеж
            log(traceback.format_exc())
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"Internal error: {error}"},
            }
        if response is not None:
            write_message(output_stream, response)


def resolve_catalog_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    environment = os.environ.get("AI_CAPABILITIES_DIR")
    if environment:
        return Path(environment).expanduser()

    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / CATALOG_DIRNAME,
        here / CATALOG_DIRNAME,
        Path.cwd() / CATALOG_DIRNAME,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MCP-сервер каталога «Возможности AI» (stdio, только чтение)."
    )
    parser.add_argument(
        "--catalog",
        help=(
            "Путь к папке каталога. По умолчанию используется переменная "
            "AI_CAPABILITIES_DIR или ../Возможности AI рядом с сервером."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить каталог и вывести статистику, не запуская MCP-сервер.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Вывести имена инструментов и выйти.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{SERVER_NAME} {SERVER_VERSION}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog_path = resolve_catalog_path(args.catalog)
    try:
        catalog = Catalog(catalog_path)
    except CatalogError as error:
        print(f"Ошибка каталога: {error}", file=sys.stderr)
        return 2

    if args.check:
        payload = {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "catalog": str(catalog.root),
            **catalog.stats(),
            "capability_ids": [item.id for item in catalog.capabilities],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.list_tools:
        for tool in TOOLS:
            print(tool["name"])
        return 0

    try:
        return serve(catalog)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
