from __future__ import annotations

import unittest

from combat_simulator import (
    AccuracyTier,
    AmmunitionProfile,
    ShootingSimulator,
    TargetProfile,
    WeaponProfile,
    effective_bullet_resistance,
)
from combat_simulator.engine import shot_distribution


class CombatEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.weapon = WeaponProfile(
            close_damage=50.0,
            minimum_damage=40.0,
            damage_falloff_start_m=20.0,
            damage_falloff_end_m=60.0,
            rounds_per_minute=600.0,
            magazine_capacity=30,
            reload_seconds=3.0,
            headshot_multiplier=1.4,
            limb_multiplier=0.8,
        )

    def test_armor_penetration_reduces_total_bullet_resistance(self) -> None:
        self.assertEqual(effective_bullet_resistance(400.0, 20.0), 320.0)
        self.assertEqual(effective_bullet_resistance(400.0, 0.0), 400.0)
        self.assertEqual(effective_bullet_resistance(400.0, -20.0), 480.0)

    def test_armor_penetration_cannot_create_negative_resistance(self) -> None:
        self.assertEqual(effective_bullet_resistance(400.0, 125.0), 0.0)

    def test_accuracy_table_is_interpolated_between_rows(self) -> None:
        distribution = shot_distribution(25.0, AccuracyTier.MEDIUM)

        self.assertAlmostEqual(distribution.hit_probability, 0.55)
        self.assertAlmostEqual(distribution.head_share, 0.45)
        self.assertAlmostEqual(distribution.body_share, 0.375)
        self.assertAlmostEqual(distribution.limb_share, 0.175)

    def test_accuracy_table_interpolates_requested_15_meter_distance(self) -> None:
        distribution = shot_distribution(15.0, AccuracyTier.MEDIUM)

        self.assertAlmostEqual(distribution.hit_probability, 0.77)
        self.assertAlmostEqual(distribution.head_share, 0.55)
        self.assertAlmostEqual(distribution.body_share, 0.325)
        self.assertAlmostEqual(distribution.limb_share, 0.125)

    def test_calculation_applies_penetration_before_vitality(self) -> None:
        result = ShootingSimulator().calculate(
            weapon=self.weapon,
            ammunition=AmmunitionProfile(armor_penetration_percent=20.0),
            target=TargetProfile(bullet_resistance=400.0, vitality_percent=10.0),
            distance_m=30.0,
            accuracy_tier=AccuracyTier.MEDIUM,
        )

        self.assertAlmostEqual(result.effective_bullet_resistance, 320.0)
        self.assertAlmostEqual(result.effective_health, 462.0)
        self.assertEqual(result.hits_to_kill, 9)
        self.assertEqual(result.bullets_to_kill, 19)
        self.assertAlmostEqual(result.ttk_seconds, 1.8)

    def test_reload_time_is_included_after_magazine_is_exhausted(self) -> None:
        tiny_magazine = WeaponProfile(
            close_damage=10.0,
            minimum_damage=10.0,
            damage_falloff_start_m=0.0,
            damage_falloff_end_m=0.0,
            rounds_per_minute=600.0,
            magazine_capacity=2,
            reload_seconds=2.0,
        )
        result = ShootingSimulator().calculate(
            weapon=tiny_magazine,
            ammunition=AmmunitionProfile(),
            target=TargetProfile(bullet_resistance=0.0, vitality_percent=0.0),
            distance_m=10.0,
            accuracy_tier=AccuracyTier.MEDIUM,
        )

        self.assertEqual(result.bullets_to_kill, 12)
        self.assertEqual(result.reloads, 5)
        self.assertAlmostEqual(result.ttk_seconds, 10.6)


if __name__ == "__main__":
    unittest.main()
