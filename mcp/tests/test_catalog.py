from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_DIR.parent
CATALOG_PATH = REPO_ROOT / "Возможности AI"
sys.path.insert(0, str(MCP_DIR))

from catalog import (  # noqa: E402
    URI_PREFIX,
    Catalog,
    CatalogError,
    level_for_score,
    normalize_level,
)


class CatalogParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = Catalog(CATALOG_PATH)

    def test_all_capabilities_are_loaded(self) -> None:
        self.assertEqual(len(self.catalog.capabilities), 8)
        self.assertEqual(
            [item.id for item in self.catalog.capabilities],
            [
                "automation",
                "data-analytics",
                "consulting",
                "text-processing",
                "verification",
                "media",
                "files",
                "development",
            ],
        )

    def test_risk_count_and_stats(self) -> None:
        stats = self.catalog.stats()
        self.assertEqual(stats["risks"], 71)
        self.assertEqual(stats["risks_by_level"]["critical"], 24)
        self.assertEqual(stats["risks_by_level"]["high"], 23)
        self.assertEqual(stats["risks_by_level"]["medium"], 24)
        self.assertEqual(stats["risks_by_level"]["low"], 0)

    def test_every_risk_is_structurally_valid(self) -> None:
        self.assertEqual(len(self.catalog.risks), 71)
        for risk in self.catalog.risks:
            with self.subTest(risk=risk.id):
                self.assertGreaterEqual(risk.probability, 1)
                self.assertLessEqual(risk.probability, 5)
                self.assertGreaterEqual(risk.impact, 1)
                self.assertLessEqual(risk.impact, 5)
                self.assertEqual(risk.score, risk.probability * risk.impact)
                self.assertEqual(risk.level, level_for_score(risk.score))
                self.assertTrue(risk.title)
                self.assertTrue(risk.risk_category)
                self.assertTrue(risk.mitigations)
                self.assertTrue(risk.source)
                self.assertRegex(risk.id, r"^[a-z0-9-]+-\d+$")

    def test_risk_matrices_with_two_dash_separators_are_parsed(self) -> None:
        # В исходных таблицах «Обработка текстовой информации» и
        # «Разработка и работа с кодом» разделитель записан как --:, а не ---:.
        self.assertEqual(len(self.catalog.get_capability("text-processing").risks), 9)
        self.assertEqual(len(self.catalog.get_capability("development").risks), 9)

    def test_known_risk_values(self) -> None:
        risk = self.catalog.get_risk("development-1")
        self.assertEqual(risk.title, "Уязвимости и небезопасный код: инъекции, слабая работа с секретами, устаревшие зависимости.")
        self.assertEqual(risk.risk_category, "Безопасность")
        self.assertEqual(risk.probability, 4)
        self.assertEqual(risk.impact, 5)
        self.assertEqual(risk.score, 20)
        self.assertEqual(risk.level, "Критический")

        file_risk = self.catalog.get_risk("files-2")
        self.assertEqual(file_risk.score, 15)
        self.assertEqual(file_risk.level, "Критический")

    def test_description_parsing(self) -> None:
        capability = self.catalog.get_capability("development")
        self.assertTrue(capability.goal.startswith("создать или изменить"))
        self.assertIn("Напиши SQL-запрос", capability.examples)
        self.assertGreaterEqual(len(capability.bullets), 5)
        self.assertIn("Генерация и объяснение кода.", capability.bullets)

    def test_risk_scale_parsing(self) -> None:
        scale = self.catalog.scale
        self.assertIn("1–4", scale.summary)
        self.assertIn("критический", scale.summary)
        self.assertEqual(len(scale.probability), 5)
        self.assertEqual(len(scale.impact), 5)
        self.assertEqual(len(scale.levels), 4)
        self.assertEqual(
            [row["level"] for row in scale.levels],
            ["Низкий", "Средний", "Высокий", "Критический"],
        )
        self.assertIn("1–4", scale.levels[0]["range"])
        self.assertTrue(scale.note)

    def test_category_lookup_by_id_and_title(self) -> None:
        for value in [
            "development",
            "Разработка и работа с кодом",
            "files",
            "Работа с файлами",
            " data-analytics ",
            "«Работа с файлами»",
            "`development`",
        ]:
            with self.subTest(value=value):
                capability = self.catalog.get_capability(value)
                self.assertTrue(capability.title)

        with self.assertRaises(CatalogError):
            self.catalog.get_capability("unknown-category")

    def test_risk_lookup_formats(self) -> None:
        for value in ["development-1", "development:1", "development#1"]:
            with self.subTest(value=value):
                self.assertEqual(self.catalog.get_risk(value).id, "development-1")
        with self.assertRaises(CatalogError):
            self.catalog.get_risk("development-999")

    def test_normalize_level(self) -> None:
        for value in ["критический", "critical", "Критическая", "CRITICAL"]:
            with self.subTest(value=value):
                self.assertEqual(normalize_level(value), "critical")
        with self.assertRaises(CatalogError):
            normalize_level("очень страшный")

    def test_filters(self) -> None:
        critical_files = self.catalog.filter_risks(
            category="files", level="critical"
        )
        self.assertEqual(
            [risk.id for risk in critical_files],
            ["files-1", "files-2", "files-5", "files-6"],
        )

        critical = self.catalog.filter_risks(level="критический")
        critical_en = self.catalog.filter_risks(level="critical")
        self.assertEqual(len(critical), 24)
        self.assertEqual(
            [risk.id for risk in critical], [risk.id for risk in critical_en]
        )

        security = self.catalog.filter_risks(risk_category="Безопасность")
        self.assertTrue(security)
        self.assertTrue(
            all("Безопасность" in risk.risk_category for risk in security)
        )

        high_impact = self.catalog.filter_risks(min_impact=5, min_score=20)
        self.assertTrue(high_impact)
        self.assertTrue(all(risk.impact == 5 for risk in high_impact))
        self.assertTrue(all(risk.score >= 20 for risk in high_impact))

    def test_filter_by_query_and_sort(self) -> None:
        results = self.catalog.filter_risks(query="prompt injection")
        self.assertEqual([risk.id for risk in results], ["automation-2"])

        by_score = self.catalog.filter_risks(sort_by="score")
        scores = [risk.score for risk in by_score]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(by_score), 71)

    def test_search(self) -> None:
        hits = self.catalog.search("утечка данных", limit=5)
        self.assertEqual(len(hits), 5)
        self.assertTrue(any(hit["type"] == "risk" for hit in hits))
        self.assertTrue(all(hit["score"] > 0 for hit in hits))

        all_hits = self.catalog.search("утечка данных", limit=None)
        self.assertGreater(len(all_hits), 5)
        scores = [hit["score"] for hit in all_hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

        development_hits = self.catalog.search(
            "код", category="development", limit=100
        )
        self.assertTrue(development_hits)
        self.assertTrue(
            all(
                hit["type"] == "risk"
                or hit["id"] == "development"
                for hit in development_hits
            )
        )

        with self.assertRaises(CatalogError):
            self.catalog.search("   ")

    def test_search_capability_and_risk_uris(self) -> None:
        hits = self.catalog.search("утечка", limit=100)
        self.assertTrue(hits)
        for hit in hits:
            self.assertTrue(hit["uri"].startswith(URI_PREFIX))
            self.assertTrue(hit["snippet"])
            self.assertIn(hit["type"], {"capability", "risk"})

    def test_resources_are_discoverable_and_readable(self) -> None:
        resources = self.catalog.resources()
        self.assertEqual(len(resources), 19)
        self.assertEqual(len({item["uri"] for item in resources}), 19)
        for resource in resources:
            with self.subTest(uri=resource["uri"]):
                mime_type, text = self.catalog.read_resource(resource["uri"])
                self.assertEqual(mime_type, resource["mimeType"])
                self.assertTrue(text)

        templates = self.catalog.resource_templates()
        self.assertEqual(len(templates), 4)
        self.assertEqual(
            {template["name"] for template in templates},
            {"category-description", "category-risks", "category", "risk"},
        )

    def test_resource_contents(self) -> None:
        mime_type, text = self.catalog.read_resource(f"{URI_PREFIX}index")
        self.assertEqual(mime_type, "application/json")
        index = json.loads(text)
        self.assertEqual(index["stats"]["risks"], 71)
        self.assertEqual(index["stats"]["capabilities"], 8)

        _, combined = self.catalog.read_resource(
            f"{URI_PREFIX}categories/development"
        )
        self.assertIn("# Разработка и работа с кодом", combined)
        self.assertIn("# Матрица рисков: Разработка и работа с кодом", combined)

        _, risk = self.catalog.read_resource(
            f"{URI_PREFIX}categories/development/risks/1"
        )
        self.assertIn("development-1", risk)
        self.assertIn("Меры снижения", risk)

        _, scale = self.catalog.read_resource(f"{URI_PREFIX}risk-scale")
        self.assertIn("Шкала оценки рисков", scale)
        self.assertIn("Вероятность", scale)

    def test_unknown_resource(self) -> None:
        with self.assertRaises(CatalogError):
            self.catalog.read_resource(f"{URI_PREFIX}unknown")
        with self.assertRaises(CatalogError):
            self.catalog.read_resource(f"{URI_PREFIX}categories/unknown")
        with self.assertRaises(CatalogError):
            self.catalog.read_resource(
                f"{URI_PREFIX}categories/development/risks/999"
            )

    def test_index_resource_has_capability_uris(self) -> None:
        index = self.catalog.index_resource()
        self.assertEqual(index["name"], "Возможности AI")
        self.assertEqual(len(index["capabilities"]), 8)
        for capability in index["capabilities"]:
            self.assertTrue(capability["description_uri"].startswith(URI_PREFIX))
            self.assertTrue(capability["risks_uri"].startswith(URI_PREFIX))
            self.assertGreaterEqual(capability["risk_count"], 8)


if __name__ == "__main__":
    unittest.main()
