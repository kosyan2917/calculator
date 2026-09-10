from __future__ import annotations

import unittest

from artcalc.solver_catalog import ArtifactCatalogCompiler, CatalogCompilerConfig


class SolverCatalogCompilerTests(unittest.TestCase):
    def test_defaults_price_finished_artifacts_without_surcharge(self) -> None:
        config = CatalogCompilerConfig()
        self.assertEqual(config.artifact_price_upgrade_level, 15)
        self.assertEqual(config.artifact_purchase_surcharge, 0)

    def test_purchase_price_includes_surcharge_and_preserves_market_price(self) -> None:
        compiler = ArtifactCatalogCompiler(
            CatalogCompilerConfig(artifact_purchase_surcharge=2_000_000)
        )
        candidate = {
            "item_id": "artifact",
            "name": "Artifact",
            "quality_tier": "rare",
            "quality_percent": 145.0,
            "price": 3_750_000,
            "stats": {"movement_speed": 1.0},
            "infections": {},
            "price_basis": "recent_7d",
            "liquidity_score": 10.0,
            "confidence_score": 100.0,
        }

        group = compiler._compile_group([candidate])

        self.assertEqual(group.market_price, 3_750_000)
        self.assertEqual(group.price, 5_750_000)


if __name__ == "__main__":
    unittest.main()
