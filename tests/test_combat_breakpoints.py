from __future__ import annotations

import unittest

from combat_simulator import AccuracyTier
from tools.analyze_combat_breakpoints import (
    calculate_hits,
    group_functional_weapons,
    load_master_weapons,
    target_for_displayed_ehp,
)


class CombatBreakpointDataTests(unittest.TestCase):
    def test_loads_current_master_primary_plus_15_snapshot(self) -> None:
        weapons = load_master_weapons()

        self.assertEqual(len(weapons), 73)
        self.assertEqual(
            {weapon.category for weapon in weapons},
            {
                "assault_rifle",
                "machine_gun",
                "sniper_rifle",
                "submachine_gun",
            },
        )
        self.assertTrue(all(weapon.profile.close_damage > 0 for weapon in weapons))

    def test_functional_duplicates_are_grouped(self) -> None:
        grouped = group_functional_weapons(load_master_weapons())

        self.assertEqual(len(grouped), 72)
        self.assertIn(
            ("HK XM8S", "XM8 (A)"),
            {weapon.names for weapon in grouped},
        )

    def test_displayed_ehp_adds_vitality_after_resistance_cap(self) -> None:
        target = target_for_displayed_ehp(500, bullet_resistance_cap=300)

        self.assertEqual(target.bullet_resistance, 300)
        self.assertEqual(target.vitality_percent, 25)
        displayed_ehp = (100 + target.bullet_resistance) * (
            100 + target.vitality_percent
        ) / 100
        self.assertEqual(displayed_ehp, 500)

    def test_accuracy_tier_does_not_change_required_hits(self) -> None:
        weapons = group_functional_weapons(load_master_weapons())[:5]
        common = {
            "weapons": weapons,
            "distance_m": 50,
            "displayed_ehp": 350,
            "bullet_resistance_cap": 300,
        }

        medium_hits = calculate_hits(
            **common,
            accuracy_tier=AccuracyTier.MEDIUM,
        )
        high_hits = calculate_hits(
            **common,
            accuracy_tier=AccuracyTier.HIGH,
        )

        self.assertEqual(high_hits, medium_hits)


if __name__ == "__main__":
    unittest.main()
