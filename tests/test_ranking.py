from __future__ import annotations

from types import SimpleNamespace
import unittest

from artcalc.ranking import BuildStrategyRanker
from tests.test_optimizer import catalog, container


def solution(build_id: str, score: float, stats: dict[str, float]) -> SimpleNamespace:
    return SimpleNamespace(
        build_id=build_id,
        preference_score=score,
        total_price=1_000_000,
        artifacts=(
            {
                "stats": stats,
                "infections": {},
            },
        ),
        to_dict=lambda: {"build_id": build_id},
    )


class BuildStrategyRankerTest(unittest.TestCase):
    def test_strategy_can_trade_small_current_loss_for_reuse(self) -> None:
        source_catalog = catalog((), (), container(1))
        ranker = BuildStrategyRanker(source_catalog)
        strongest = solution("strongest", 10.0, {"movement_speed": 5.0})
        universal = solution(
            "universal",
            9.8,
            {
                "movement_speed": 3.0,
                "vitality": 5.0,
                "health_regeneration": 2.0,
            },
        )

        current = ranker.rank((universal, strongest), "best_now", 1)
        upgrade = ranker.rank((universal, strongest), "upgrade", 1)

        self.assertEqual(current[0].solution.build_id, "strongest")
        self.assertEqual(upgrade[0].solution.build_id, "universal")
        self.assertAlmostEqual(upgrade[0].current_stat_loss_percent, 2.0)
        self.assertGreater(upgrade[0].upgrade_potential, current[0].upgrade_potential)

    def test_upgrade_strategy_does_not_cross_loss_limit(self) -> None:
        source_catalog = catalog((), (), container(1))
        ranker = BuildStrategyRanker(source_catalog)
        strongest = solution("strongest", 10.0, {"movement_speed": 5.0})
        too_weak = solution(
            "too-weak",
            8.0,
            {
                "movement_speed": 3.0,
                "vitality": 5.0,
                "health_regeneration": 2.0,
            },
        )

        ranked = ranker.rank((too_weak, strongest), "upgrade", 2)

        self.assertEqual([item.solution.build_id for item in ranked], ["strongest"])


if __name__ == "__main__":
    unittest.main()
