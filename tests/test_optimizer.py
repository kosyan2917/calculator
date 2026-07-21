from __future__ import annotations

import unittest

from artcalc.optimizer import ArtifactBuildOptimizer, OptimizationRequest, OptimizerConfig
from artcalc.solver_catalog import ArtifactGroup, SolverCatalog
from artcalc.stat_model import MechanicsConfig


def group(
    group_id: str,
    stats_low: dict[str, float],
    stats_high: dict[str, float],
    infections_low: dict[str, float] | None = None,
    infections_high: dict[str, float] | None = None,
    low: int = 16000,
    high: int = 17500,
) -> ArtifactGroup:
    return ArtifactGroup(
        group_id=group_id,
        item_id=group_id,
        name=group_id,
        quality_tier="legendary",
        quality_low=low,
        quality_high=high,
        price=1_000_000,
        stats_low=stats_low,
        stats_high=stats_high,
        infections_low=infections_low or {},
        infections_high=infections_high or {},
    )


def armor(item_id: str, stats: dict[str, float]) -> dict:
    return {
        "item_id": item_id,
        "base_id": item_id,
        "name": item_id,
        "rank": "\u041c\u0430\u0441\u0442\u0435\u0440",
        "category": "armor/combat",
        "upgrade_level": 15,
        "stats": stats,
    }


def container(capacity: int, stats: dict[str, float] | None = None) -> dict:
    return {
        "container_id": "container",
        "name": "container",
        "rank": "\u041c\u0430\u0441\u0442\u0435\u0440",
        "capacity": capacity,
        "inner_protection": 0.0,
        "effectiveness": 100.0,
        "stats": stats or {},
    }


def catalog(groups: tuple[ArtifactGroup, ...], armors: tuple[dict, ...], test_container: dict) -> SolverCatalog:
    mechanics = MechanicsConfig()
    return SolverCatalog(
        generated_at="test",
        artifact_upgrade_level=15,
        artifact_price_upgrade_level=0,
        min_quality_percent=95.0,
        mechanics={
            "quality_policy": mechanics.quality_policy,
            "apply_container_effectiveness": mechanics.apply_container_effectiveness,
            "container_effectiveness_affects_infections": mechanics.container_effectiveness_affects_infections,
            "frost_ignores_inner_protection": mechanics.frost_ignores_inner_protection,
            "container_infections_are_protected": mechanics.container_infections_are_protected,
            "base_infection_output": mechanics.base_infection_output,
        },
        artifact_groups=groups,
        containers=(test_container,),
        armors=armors,
    )


class ArtifactBuildOptimizerTest(unittest.TestCase):
    def test_quality_is_optimized_at_infection_boundary(self) -> None:
        speed = group(
            "speed",
            {"movement_speed": 1.0},
            {"movement_speed": 2.0},
            {"radiation": 0.0},
            {"radiation": 0.1},
        )
        test_catalog = catalog(
            (speed,),
            (armor("plain", {}),),
            container(7, {"radiation": -0.11}),
        )
        optimizer = ArtifactBuildOptimizer(
            test_catalog,
            OptimizerConfig(
                time_limit_per_solve=1.0,
                max_solutions_per_container=1,
                infection_safety_margin=0.0,
            ),
        )

        result = optimizer.search(
            OptimizationRequest(
                budget=7_000_000,
                preferences={"speed": 2.0},
                max_results=1,
            )
        )

        solution = result.solutions[0]
        qualities = sorted((item["quality_percent"] for item in solution.artifacts), reverse=True)
        self.assertEqual(qualities[:6], [175.0] * 6)
        self.assertAlmostEqual(qualities[6], 161.5, places=2)
        self.assertTrue(solution.infection["valid"])

    def test_infection_safety_margin_is_preserved_in_exact_result(self) -> None:
        speed = group(
            "speed",
            {"movement_speed": 1.0},
            {"movement_speed": 2.0},
            {"radiation": 0.0},
            {"radiation": 0.1},
        )
        optimizer = ArtifactBuildOptimizer(
            catalog(
                (speed,),
                (armor("plain", {}),),
                container(7, {"radiation": -0.11}),
            ),
            OptimizerConfig(
                time_limit_per_solve=1.0,
                max_solutions_per_container=1,
                infection_safety_margin=0.05,
            ),
        )

        solution = optimizer.search(
            OptimizationRequest(
                budget=7_000_000,
                preferences={"speed": 2.0},
                max_results=1,
            )
        ).solutions[0]

        self.assertGreaterEqual(solution.infection["by_type"]["radiation"]["margin"], 0.05 - 1e-9)

    def test_armor_changes_durability_choice(self) -> None:
        bullet = group("bullet", {"bullet_resistance": 20.0}, {"bullet_resistance": 20.0})
        vitality = group("vitality", {"vitality": 10.0}, {"vitality": 10.0})
        test_catalog = catalog(
            (bullet, vitality),
            (armor("plain", {}), armor("bullet_armor", {"bullet_resistance": 400.0})),
            container(1),
        )
        optimizer = ArtifactBuildOptimizer(
            test_catalog,
            OptimizerConfig(time_limit_per_solve=1.0, max_solutions_per_container=1),
        )

        plain = optimizer.search(
            OptimizationRequest(
                budget=1_000_000,
                preferences={"durability": 2.0},
                armor_ids=("plain",),
                max_results=1,
            )
        ).solutions[0]
        armored = optimizer.search(
            OptimizationRequest(
                budget=1_000_000,
                preferences={"durability": 2.0},
                armor_ids=("bullet_armor",),
                max_results=1,
            )
        ).solutions[0]

        self.assertEqual(plain.artifacts[0]["item_id"], "bullet")
        self.assertEqual(armored.artifacts[0]["item_id"], "vitality")
        self.assertAlmostEqual(plain.derived["effective_durability"], 120.0)
        self.assertAlmostEqual(armored.derived["effective_durability"], 550.0)

    def test_equipment_exclusions_are_applied_by_the_core(self) -> None:
        test_catalog = catalog(
            (group("speed", {"movement_speed": 1.0}, {"movement_speed": 1.0}),),
            (armor("keep", {}), armor("drop", {})),
            container(1),
        )
        optimizer = ArtifactBuildOptimizer(test_catalog)

        armors = optimizer._eligible_armors(
            OptimizationRequest(
                budget=1_000_000,
                preferences={"speed": 1.0},
                excluded_armor_ids=("drop",),
            )
        )

        self.assertEqual([item["item_id"] for item in armors], ["keep"])
        with self.assertRaisesRegex(ValueError, "No containers match"):
            optimizer._eligible_containers(
                OptimizationRequest(
                    budget=1_000_000,
                    preferences={"speed": 1.0},
                    excluded_container_ids=("container",),
                )
            )

    def test_preference_cap_spends_remaining_slots_on_other_stats(self) -> None:
        heavy = group("heavy", {"carry_weight": 100.0}, {"carry_weight": 100.0})
        fast = group("fast", {"movement_speed": 6.0}, {"movement_speed": 6.0})
        test_catalog = catalog(
            (heavy, fast),
            (armor("plain", {}),),
            container(2),
        )
        optimizer = ArtifactBuildOptimizer(
            test_catalog,
            OptimizerConfig(time_limit_per_solve=1.0, max_solutions_per_container=1),
        )

        solution = optimizer.search(
            OptimizationRequest(
                budget=2_000_000,
                preferences={"weight": 0.5, "speed": 0.5},
                preference_caps={"weight": 100.0},
                max_results=1,
            )
        ).solutions[0]

        self.assertEqual({item["item_id"] for item in solution.artifacts}, {"heavy", "fast"})
        self.assertAlmostEqual(solution.stats["carry_weight"], 100.0)

        weight_only = optimizer.search(
            OptimizationRequest(
                budget=2_000_000,
                preferences={"weight": 0.5},
                preference_caps={"weight": 100.0},
                max_results=1,
            )
        ).solutions[0]

        self.assertEqual({item["item_id"] for item in weight_only.artifacts}, {"heavy", "fast"})
        self.assertAlmostEqual(weight_only.stats["carry_weight"], 100.0)


if __name__ == "__main__":
    unittest.main()
