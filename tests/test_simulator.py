from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from services.simulator.app import app


class SimulatorServiceTests(unittest.TestCase):
    def test_health_contract(self) -> None:
        response = TestClient(app).get("/api/simulator/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "simulator"})

    def test_shooting_contract(self) -> None:
        response = TestClient(app).post(
            "/api/simulator/shooting",
            json={
                "weapon": {
                    "close_damage": 50,
                    "minimum_damage": 40,
                    "damage_falloff_start_m": 20,
                    "damage_falloff_end_m": 60,
                    "rounds_per_minute": 600,
                    "magazine_capacity": 30,
                    "reload_seconds": 3,
                    "headshot_multiplier": 1.4,
                    "limb_multiplier": 0.8,
                },
                "ammunition": {
                    "armor_penetration_percent": 20,
                    "damage_modifier_percent": 0,
                },
                "target": {
                    "bullet_resistance": 400,
                    "vitality_percent": 10,
                },
                "distance_m": 30,
                "accuracy_tier": "medium",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["bullets_to_kill"], 19)
        self.assertAlmostEqual(payload["ttk_seconds"], 1.8)
        self.assertAlmostEqual(payload["effective_bullet_resistance"], 320)


if __name__ == "__main__":
    unittest.main()
