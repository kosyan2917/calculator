from __future__ import annotations

import unittest

from artcalc.upgrade_potential import BuildUpgradePotentialAnalyzer
from tests.test_upgrade import catalog


class UpgradePotentialAnalyzerTest(unittest.TestCase):
    def test_counts_reusable_roles_and_larger_compatible_containers(self) -> None:
        source = catalog()
        build = {
            "container": source.containers[0],
            "artifacts": (
                {
                    "item_id": "fast",
                    "name": "fast",
                    "stats": {"movement_speed": 2.0, "carry_weight": 10.0},
                    "infections": {},
                },
            ),
        }
        analyzer = BuildUpgradePotentialAnalyzer(source)

        potential = analyzer.analyze(build)

        self.assertEqual(potential.reusable_roles, ("speed", "weight"))
        self.assertEqual(potential.compatible_container_count, 1)
        self.assertEqual(potential.larger_container_count, 1)
        self.assertEqual(potential.best_container_upgrade["container_id"], "larger")
        self.assertGreater(potential.artifact_reuse_score, 0.0)
        self.assertGreater(potential.container_upgrade_score, 0.0)

        blocked = analyzer.analyze(build, ("larger",))
        self.assertEqual(blocked.compatible_container_count, 0)
        self.assertEqual(blocked.container_upgrade_score, 0.0)


if __name__ == "__main__":
    unittest.main()
