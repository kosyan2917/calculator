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
        self.assertGreater(len(payload["containers"]), 0)
        self.assertIn("durability", {metric["key"] for metric in payload["metrics"]})

    def test_optimize_returns_exactly_validated_builds(self) -> None:
        catalog = self.client.get("/api/catalog").json()
        response = self.client.post(
            "/api/optimize",
            json={
                "budget": 10_000_000,
                "armor_id": catalog["armors"][0]["id"],
                "container_id": catalog["containers"][0]["id"],
                "preferences": {"speed": 4, "regen": 2},
                "max_results": 3,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("solutions", payload)
        for build in payload["solutions"]:
            self.assertLessEqual(build["total_price"], 10_000_000)
            self.assertTrue(build["infection"]["valid"])

    def test_optimize_rejects_empty_preferences(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json={"budget": 10_000_000, "preferences": {"speed": 0}},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
