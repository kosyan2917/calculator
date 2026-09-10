from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from web.backend.app import app, get_optimization_cache


class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_catalog_exposes_hard_constraint_contract(self) -> None:
        response = self.client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("preference_levels", payload)
        self.assertNotIn("strategies", payload)
        self.assertGreater(len(payload["armors"]), 0)
        self.assertEqual(len(payload["containers"]), 27)
        self.assertGreater(len(payload["artifacts"]), 0)
        self.assertIn("durability", {metric["key"] for metric in payload["metrics"]})
        self.assertEqual(
            {metric["key"] for metric in payload["metrics"] if metric["group"] == "main"},
            {"durability", "speed"},
        )
        self.assertEqual({item["category"] for item in payload["containers"]}, {"containers", "backpacks"})
        tri_zip = next(item for item in payload["containers"] if item["id"] == "lny1")
        self.assertEqual(tri_zip["capacity"], 5)

    def test_nonlinear_requirement_returns_only_valid_builds(self) -> None:
        get_optimization_cache().clear()
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 50_000_000,
                "armor_id": "2ovr0",
                "container_id": "g35n",
                "targets": {"durability": 450.0},
                "max_results": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["diagnostics"]["cache_hit"])
        self.assertEqual(payload["diagnostics"]["engine"], "frontier_v3")
        self.assertGreater(len(payload["solutions"]), 0)
        self.assertEqual(payload["request"]["targets"], {"durability": 450.0})
        self.assertEqual(payload["request"]["max_quality_tier"], "exclusive")
        for build in payload["solutions"]:
            self.assertLessEqual(build["total_price"], 50_000_000)
            self.assertTrue(build["infection"]["valid"])
            self.assertGreaterEqual(build["derived"]["effective_durability"], 450.0)
            self.assertNotIn("preference_score", build)
            self.assertNotIn("objective_score", build)
            self.assertIn("score", build["upgrade_potential"])
            self.assertIn("artifact_reuse_score", build["upgrade_potential"])
            self.assertIn("container_upgrade_score", build["upgrade_potential"])
            self.assertIn("loadout_stats", build)
            self.assertNotEqual(build["loadout_stats"].get("bullet_resistance", 0), build["stats"]["bullet_resistance"])

        cached_response = self.client.post(
            "/api/optimize",
            json={
                "budget": 50_000_000,
                "armor_id": "2ovr0",
                "container_id": "g35n",
                "targets": {"durability": 450.0},
                "max_results": 2,
            },
        )
        self.assertEqual(cached_response.status_code, 200, cached_response.text)
        cached_payload = cached_response.json()
        self.assertTrue(cached_payload["diagnostics"]["cache_hit"])
        self.assertEqual(cached_payload["solutions"], payload["solutions"])

    def test_optimize_rejects_empty_requirements(self) -> None:
        response = self.client.post("/api/optimize", json={"budget": 10_000_000, "targets": {}})
        self.assertEqual(response.status_code, 422)

    def test_budget_contract_accepts_arbitrary_amount_and_unlimited(self) -> None:
        from web.backend.app import OptimizePayload
        self.assertEqual(OptimizePayload(budget=100_001, targets={"speed": 0}).budget, 100_001)
        self.assertIsNone(OptimizePayload(budget=None).budget)
        self.assertEqual(self.client.post("/api/optimize", json={"budget": 99_999, "targets": {"speed": 0}}).status_code, 422)

    def test_optimize_rejects_selected_and_excluded_armor(self) -> None:
        armor_id = self.client.get("/api/catalog").json()["armors"][0]["id"]
        response = self.client.post(
            "/api/optimize",
            json={"budget": 10_000_000, "armor_id": armor_id, "targets": {"speed": 0.0}, "excluded_armor_ids": [armor_id]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("No armors match", response.json()["detail"])

    def test_optimize_rejects_unknown_requirement(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json={"budget": 10_000_000, "targets": {"unknown_stat": 1.0}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("unknown_stat", response.json()["detail"])

    def test_optimize_rejects_unknown_maximum_quality(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 10_000_000,
                "targets": {"speed": 0.0},
                "max_quality_tier": "mythical",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("mythical", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
