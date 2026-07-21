from __future__ import annotations

import unittest

from artcalc.solver_catalog import ArtifactGroup, SolverCatalog
from artcalc.stat_model import MechanicsConfig
from artcalc.upgrade import UpgradePlanner, UpgradePlannerConfig, UpgradePlanningRequest


def artifact(group_id: str, speed: float) -> ArtifactGroup:
    return ArtifactGroup(
        group_id=group_id,
        item_id=group_id,
        name=group_id,
        quality_tier="legendary",
        quality_low=16000,
        quality_high=17500,
        price=1_000_000,
        stats_low={"movement_speed": speed},
        stats_high={"movement_speed": speed},
        infections_low={},
        infections_high={},
    )


def equipment(container_id: str, capacity: int) -> dict:
    return {
        "container_id": container_id,
        "name": container_id,
        "rank": "Мастер",
        "category": "containers",
        "capacity": capacity,
        "inner_protection": 0.0,
        "effectiveness": 100.0,
        "stats": {},
    }


def catalog() -> SolverCatalog:
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
        artifact_groups=(artifact("slow", 1.0), artifact("fast", 2.0)),
        containers=(equipment("current", 1), equipment("larger", 2)),
        armors=(
            {
                "item_id": "armor",
                "base_id": "armor",
                "name": "armor",
                "rank": "Мастер",
                "category": "armor/combat",
                "upgrade_level": 15,
                "stats": {},
            },
        ),
    )


class UpgradePlannerTest(unittest.TestCase):
    def test_keeps_owned_artifact_and_uses_free_container_change(self) -> None:
        source_catalog = catalog()
        current_build = {
            "armor": source_catalog.armors[0],
            "container": source_catalog.containers[0],
            "artifacts": (
                {
                    "group_id": "slow",
                    "item_id": "slow",
                    "name": "slow",
                    "quality_tier": "legendary",
                    "quality_percent": 175.0,
                    "price": 1_000_000,
                    "market_price": 1_000_000,
                },
            ),
            "stats": {"movement_speed": 1.0},
            "derived": {"effective_durability": 100.0, "run_speed_total": 101.0, "healing_per_second": 0.0},
        }
        planner = UpgradePlanner(
            source_catalog,
            UpgradePlannerConfig(
                extra_budgets=(1_000_000, 2_000_000),
                max_plans_per_budget=2,
                time_limit_per_solve=1.0,
                nonlinear_iterations=1,
            ),
        )

        result = planner.plan(
            UpgradePlanningRequest(
                current_build=current_build,
                preferences={"speed": 2.0},
                preference_caps={},
                targets={},
            )
        )

        changed = next(plan for plan in result.plans if plan.container_changed)
        self.assertEqual(changed.kept_count, 1)
        self.assertEqual(changed.purchase_cost, 1_000_000)
        self.assertEqual(changed.resale_credit, 0)
        self.assertEqual(changed.removed_artifacts, ())
        self.assertEqual([item["item_id"] for item in changed.added_artifacts], ["fast"])
        self.assertEqual(changed.result_build["container"]["container_id"], "larger")
        build_ids = [plan.result_build["build_id"] for plan in result.plans]
        self.assertEqual(len(build_ids), len(set(build_ids)))
        self.assertGreaterEqual(result.diagnostics["duplicate_plans_removed"], 1)


if __name__ == "__main__":
    unittest.main()
