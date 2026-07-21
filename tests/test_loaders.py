from __future__ import annotations

from pathlib import Path
import unittest

from artcalc.loaders import load_containers


ROOT = Path(__file__).resolve().parents[1]


class ContainerLoaderTests(unittest.TestCase):
    def test_ranked_backpacks_and_explicit_exceptions_are_loaded_as_containers(self) -> None:
        containers = load_containers(
            ROOT / "stalzone-database",
            "ru",
            {"\u0412\u0435\u0442\u0435\u0440\u0430\u043d", "\u041c\u0430\u0441\u0442\u0435\u0440"},
            {"lny1"},
        )
        by_name = {item["name"]: item for item in containers}

        rig = by_name["\u0420\u0430\u0437\u0433\u0440\u0443\u0437\u043a\u0430 ADR-WRBT"]
        self.assertEqual(rig["category"], "backpacks")
        self.assertEqual(rig["equipment_class"], "light")
        self.assertEqual(rig["capacity"], 4)
        self.assertAlmostEqual(rig["stats"]["movement_speed"], 6.2)
        self.assertAlmostEqual(rig["stats"]["carry_weight"], 40.0)

        backpack = by_name["\u0420\u044e\u043a\u0437\u0430\u043a Secret Valley 35"]
        self.assertEqual(backpack["category"], "backpacks")
        self.assertEqual(backpack["equipment_class"], "medium")
        self.assertEqual(backpack["capacity"], 6)
        self.assertAlmostEqual(backpack["stats"]["carry_weight"], 81.0)

        tri_zip = by_name["\u0428\u0442\u0443\u0440\u043c\u043e\u0432\u043e\u0439 \u0440\u044e\u043a\u0437\u0430\u043a Tri-Zip"]
        self.assertEqual(tri_zip["rank"], "\u0421\u0442\u0430\u043b\u043a\u0435\u0440")
        self.assertEqual(tri_zip["equipment_class"], "medium")
        self.assertEqual(tri_zip["capacity"], 5)
        self.assertAlmostEqual(tri_zip["inner_protection"], 60.000004)
        self.assertAlmostEqual(tri_zip["effectiveness"], 78.5)
        self.assertAlmostEqual(tri_zip["stats"]["carry_weight"], 56.0)


if __name__ == "__main__":
    unittest.main()
