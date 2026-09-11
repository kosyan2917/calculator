from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from web.backend.app import app, get_catalog, get_optimization_cache


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
        self.assertEqual(len(payload["containers"]), 28)
        self.assertGreater(len(payload["artifacts"]), 0)
        self.assertIn("durability", {metric["key"] for metric in payload["metrics"]})
        self.assertEqual(
            {metric["key"] for metric in payload["metrics"] if metric["group"] == "main"},
            {"durability", "speed"},
        )
        self.assertEqual({item["category"] for item in payload["containers"]}, {"containers", "backpacks"})
        tri_zip = next(item for item in payload["containers"] if item["id"] == "lny1")
        self.assertEqual(tri_zip["capacity"], 5)
        self.assertTrue({"m03w7", "wj4no"} <= {item["id"] for item in payload["armors"]})
        self.assertIn("yq90", {item["id"] for item in payload["containers"]})

    def test_master_selection_restricts_search_inputs_in_both_modes(self) -> None:
        data = get_catalog()
        for reaction in (None, "electricity"):
            with self.subTest(reaction=reaction):
                get_optimization_cache().clear()
                generator = Mock()
                generator.search.side_effect = ValueError("test search inputs")
                factory = "get_reaction_generator" if reaction else "get_optimizer"
                with patch(f"web.backend.app.{factory}", return_value=generator):
                    response = self.client.post("/api/optimize", json={
                        "budget": 10_000_000, "targets": {"speed": 0},
                        "armor_rank": "master", "container_rank": "master",
                        "excluded_container_ids": ["g35n"], "active_reaction": reaction,
                    })
                self.assertEqual(response.status_code, 422)
                request = generator.search.call_args.args[0]
                self.assertEqual(set(request.armor_ids),
                                 {a["item_id"] for a in data.armors if a["rank"] == "\u041c\u0430\u0441\u0442\u0435\u0440"})
                self.assertEqual(set(request.container_ids),
                                 {c["container_id"] for c in data.containers if c["rank"] == "\u041c\u0430\u0441\u0442\u0435\u0440"})
                self.assertTrue(request.armor_ids)
                self.assertTrue(request.container_ids)
                self.assertFalse({"m03w7", "wj4no"} & set(request.armor_ids))
                self.assertNotIn("yq90", request.container_ids)
                self.assertEqual(request.excluded_container_ids, ("g35n",))

    def test_conflicting_equipment_rank_does_not_become_unrestricted(self) -> None:
        for selection in ({"armor_id": "m03w7", "armor_rank": "master"},
                          {"container_id": "yq90", "container_rank": "master"}):
            response = self.client.post("/api/optimize", json={
                "budget": 10_000_000, "targets": {"speed": 0}, **selection,
            })
            self.assertEqual(response.status_code, 422)
            self.assertIn("selected rank", response.json()["detail"])

    def test_unknown_equipment_rank_is_rejected(self) -> None:
        response = self.client.post("/api/optimize", json={
            "budget": 10_000_000, "targets": {"speed": 0}, "armor_rank": "unknown",
        })
        self.assertEqual(response.status_code, 422)

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
