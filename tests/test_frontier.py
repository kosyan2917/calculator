from __future__ import annotations

import unittest

from artcalc.frontier import FrontierBuildGenerator, FrontierGeneratorConfig
from artcalc.optimizer import OptimizationRequest, OptimizerConfig
from tests.test_optimizer import armor, catalog, container, group


class FrontierBuildGeneratorTest(unittest.TestCase):
    def test_finds_hybrid_between_speed_and_durability_extremes(self) -> None:
        fast = group("fast", {"movement_speed": 4.0}, {"movement_speed": 4.0})
        tank = group("tank", {"vitality": 10.0}, {"vitality": 10.0})
        source_catalog = catalog(
            (fast, tank),
            (armor("armored", {"bullet_resistance": 400.0}),),
            container(2),
        )
        generator = FrontierBuildGenerator(
            source_catalog,
            FrontierGeneratorConfig(sweep_points=1, solutions_per_point=1),
            solver_config=OptimizerConfig(
                time_limit_per_solve=1.0,
                nonlinear_iterations=2,
            ),
        )

        result = generator.search(
            OptimizationRequest(
                budget=2_000_000,
                targets={"durability": 500.0, "speed": 0.0},
                max_results=6,
            )
        )

        points = {
            (
                round(solution.stats.get("movement_speed", 0.0), 2),
                round(solution.derived["effective_durability"], 2),
            )
            for solution in result.solutions
        }
        self.assertIn((8.0, 500.0), points)
        self.assertIn((4.0, 550.0), points)
        self.assertIn((0.0, 600.0), points)
        self.assertEqual(result.diagnostics["engine"], "frontier_v3")


if __name__ == "__main__":
    unittest.main()
