from __future__ import annotations

import unittest
from dataclasses import replace

from artcalc.optimizer import ArtifactBuildOptimizer, OptimizationRequest, OptimizerConfig
from artcalc.solver_catalog import ArtifactGroup, SolverCatalog
from artcalc.stat_model import MechanicsConfig


def group(group_id: str, stats_low: dict[str, float], stats_high: dict[str, float], infections_low: dict[str, float] | None = None, infections_high: dict[str, float] | None = None, low: int = 16000, high: int = 17500) -> ArtifactGroup:
    return ArtifactGroup(group_id=group_id, item_id=group_id, name=group_id, quality_tier="legendary", quality_low=low, quality_high=high, price=1_000_000, stats_low=stats_low, stats_high=stats_high, infections_low=infections_low or {}, infections_high=infections_high or {})


def armor(item_id: str, stats: dict[str, float]) -> dict:
    return {"item_id": item_id, "base_id": item_id, "name": item_id, "rank": "Мастер", "category": "armor/combat", "upgrade_level": 15, "stats": stats}


def container(capacity: int, stats: dict[str, float] | None = None) -> dict:
    return {"container_id": "container", "name": "container", "rank": "Мастер", "capacity": capacity, "inner_protection": 0.0, "effectiveness": 100.0, "stats": stats or {}}


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
    def optimizer(self, groups: tuple[ArtifactGroup, ...], armors: tuple[dict, ...], test_container: dict) -> ArtifactBuildOptimizer:
        return ArtifactBuildOptimizer(catalog(groups, armors, test_container), OptimizerConfig(time_limit_per_solve=1.0, max_solutions_per_container=2))

    def test_owned_artifact_group_can_only_be_used_once(self) -> None:
        owned = replace(group("owned", {"movement_speed": 10.0}, {"movement_speed": 10.0}), price=0, max_count=1, owned_instance_id="owned-0", market_price=1_000_000)
        filler = group("filler", {"movement_speed": 0.0}, {"movement_speed": 0.0})
        solution = self.optimizer((owned, filler), (armor("plain", {}),), container(2)).search(OptimizationRequest(budget=1_000_000, targets={"speed": 0.0}, max_results=1)).solutions[0]
        self.assertEqual(sum(item["owned_instance_id"] is not None for item in solution.artifacts), 1)
        self.assertEqual(solution.total_price, 1_000_000)

    def test_quality_is_solved_at_required_infection_boundary(self) -> None:
        speed = group("speed", {"movement_speed": 1.0}, {"movement_speed": 2.0}, {"radiation": 0.0}, {"radiation": 0.1})
        optimizer = ArtifactBuildOptimizer(catalog((speed,), (armor("plain", {}),), container(7, {"radiation": -0.11})), OptimizerConfig(time_limit_per_solve=1.0, max_solutions_per_container=1, infection_safety_margin=0.0))
        solution = optimizer.search(OptimizationRequest(budget=7_000_000, targets={"speed": 13.1}, max_results=1)).solutions[0]
        qualities = sorted((item["quality_percent"] for item in solution.artifacts), reverse=True)
        self.assertEqual(qualities[:6], [175.0] * 6)
        self.assertAlmostEqual(qualities[6], 161.5, places=2)
        self.assertTrue(solution.infection["valid"])

    def test_infection_safety_margin_is_preserved(self) -> None:
        speed = group("speed", {"movement_speed": 1.0}, {"movement_speed": 2.0}, {"radiation": 0.0}, {"radiation": 0.1})
        optimizer = ArtifactBuildOptimizer(catalog((speed,), (armor("plain", {}),), container(7, {"radiation": -0.11})), OptimizerConfig(time_limit_per_solve=1.0, max_solutions_per_container=1, infection_safety_margin=0.05))
        solution = optimizer.search(OptimizationRequest(budget=7_000_000, targets={"speed": 7.0}, max_results=1)).solutions[0]
        self.assertGreaterEqual(solution.infection["by_type"]["radiation"]["margin"], 0.05 - 1e-9)

    def test_armor_is_part_of_nonlinear_durability_constraint(self) -> None:
        bullet = replace(group("bullet", {"bullet_resistance": 20.0}, {"bullet_resistance": 20.0}), price=1_500_000)
        vitality = group("vitality", {"vitality": 10.0}, {"vitality": 10.0})
        optimizer = self.optimizer((bullet, vitality), (armor("plain", {}), armor("bullet_armor", {"bullet_resistance": 400.0})), container(1))
        plain = optimizer.search(OptimizationRequest(budget=2_000_000, targets={"durability": 120.0}, armor_ids=("plain",), max_results=1)).solutions[0]
        armored = optimizer.search(OptimizationRequest(budget=2_000_000, targets={"durability": 550.0}, armor_ids=("bullet_armor",), max_results=1)).solutions[0]
        self.assertEqual(plain.artifacts[0]["item_id"], "bullet")
        self.assertEqual(armored.artifacts[0]["item_id"], "vitality")
        self.assertGreaterEqual(plain.derived["effective_durability"], 120.0)
        self.assertGreaterEqual(armored.derived["effective_durability"], 550.0)

    def test_unspecified_stats_do_not_affect_cheapest_solution(self) -> None:
        enough = group("enough", {"movement_speed": 2.0}, {"movement_speed": 2.0})
        excessive = replace(group("excessive", {"movement_speed": 10.0, "carry_weight": 200.0}, {"movement_speed": 10.0, "carry_weight": 200.0}), price=3_000_000)
        solution = self.optimizer((enough, excessive), (armor("plain", {}),), container(1)).search(OptimizationRequest(budget=3_000_000, targets={"speed": 2.0}, max_results=1)).solutions[0]
        self.assertEqual(solution.artifacts[0]["item_id"], "enough")
        self.assertEqual(set(solution.metrics), {"speed"})

    def test_minimize_direction_and_run_speed_bonus_are_respected(self) -> None:
        good = group("good", {"bleeding_output": -1.0, "movement_speed": 3.0, "sprint_speed": 2.0}, {"bleeding_output": -1.0, "movement_speed": 3.0, "sprint_speed": 2.0})
        bad = group("bad", {"bleeding_output": 1.0}, {"bleeding_output": 1.0})
        solution = self.optimizer((good, bad), (armor("plain", {}),), container(1)).search(OptimizationRequest(budget=1_000_000, targets={"bleeding_output": -0.5, "run_speed": 5.0}, max_results=1)).solutions[0]
        self.assertEqual(solution.artifacts[0]["item_id"], "good")
        self.assertLessEqual(solution.metrics["bleeding_output"], -0.5)
        self.assertAlmostEqual(solution.metrics["run_speed"], 5.0)

    def test_equipment_exclusions_are_applied_by_the_core(self) -> None:
        optimizer = self.optimizer((group("speed", {"movement_speed": 1.0}, {"movement_speed": 1.0}),), (armor("keep", {}), armor("drop", {})), container(1))
        request = OptimizationRequest(budget=1_000_000, targets={"speed": 1.0}, excluded_armor_ids=("drop",))
        self.assertEqual([item["item_id"] for item in optimizer._eligible_armors(request)], ["keep"])
        with self.assertRaisesRegex(ValueError, "No containers match"):
            optimizer._eligible_containers(replace(request, excluded_container_ids=("container",)))


if __name__ == "__main__":
    unittest.main()
