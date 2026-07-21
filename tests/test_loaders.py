from __future__ import annotations

from pathlib import Path
import unittest

from artcalc.loaders import load_containers


ROOT = Path(__file__).resolve().parents[1]


class ContainerLoaderTests(unittest.TestCase):
    def test_veteran_and_master_backpacks_are_loaded_as_containers(self) -> None:
        containers = load_containers(
            ROOT / "stalzone-database",
            "ru",
            {"\u0412\u0435\u0442\u0435\u0440\u0430\u043d", "\u041c\u0430\u0441\u0442\u0435\u0440"},
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


if __name__ == "__main__":
    unittest.main()
