from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from web.backend.app import app


class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_catalog_exposes_ui_contract(self) -> None:
        response = self.client.get("/api/catalog")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["preference_levels"]), 5)
        self.assertGreater(len(payload["armors"]), 0)
        self.assertEqual(len(payload["containers"]), 26)
        self.assertGreater(len(payload["artifacts"]), 0)
        self.assertEqual(len(payload["artifacts"]), len({item["id"] for item in payload["artifacts"]}))
        self.assertIn("durability", {metric["key"] for metric in payload["metrics"]})
        self.assertEqual(
            {item["category"] for item in payload["containers"]},
            {"containers", "backpacks"},
        )
        backpack = next(item for item in payload["containers"] if item["name"] == "Рюкзак Secret Valley 35")
        self.assertEqual(backpack["equipment_class"], "medium")
        self.assertEqual(backpack["capacity"], 6)
        tri_zip = next(item for item in payload["containers"] if item["id"] == "lny1")
        self.assertEqual(tri_zip["name"], "\u0428\u0442\u0443\u0440\u043c\u043e\u0432\u043e\u0439 \u0440\u044e\u043a\u0437\u0430\u043a Tri-Zip")
        self.assertEqual(tri_zip["rank"], "\u0421\u0442\u0430\u043b\u043a\u0435\u0440")
        self.assertEqual(tri_zip["capacity"], 5)

    def test_optimize_returns_exactly_validated_builds(self) -> None:
        catalog = self.client.get("/api/catalog").json()
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 10_000_000,
                "armor_id": catalog["armors"][0]["id"],
                "container_id": catalog["containers"][0]["id"],
                "preferences": {"speed": 4, "regen": 2, "weight": 2},
                "targets": {"weight": 10.0},
                "max_results": 3,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("solutions", payload)
        self.assertEqual(payload["request"]["preference_caps"]["weight"], 100.0)
        self.assertEqual(payload["request"]["targets"]["weight"], 10.0)
        for build in payload["solutions"]:
            self.assertLessEqual(build["total_price"], 10_000_000)
            self.assertTrue(build["infection"]["valid"])
            self.assertGreaterEqual(build["stats"].get("carry_weight", 0.0), 10.0)

    def test_optimize_rejects_empty_preferences(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json={"budget": 10_000_000, "preferences": {"speed": 0}},
        )

        self.assertEqual(response.status_code, 422)

    def test_optimize_rejects_selected_and_excluded_armor(self) -> None:
        armor_id = self.client.get("/api/catalog").json()["armors"][0]["id"]
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 10_000_000,
                "armor_id": armor_id,
                "preferences": {"speed": 4},
                "excluded_armor_ids": [armor_id],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("No armors match", response.json()["detail"])

    def test_optimize_rejects_unknown_target(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 10_000_000,
                "preferences": {"speed": 4},
                "targets": {"unknown_stat": 1.0},
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("unknown_stat", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
