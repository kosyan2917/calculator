from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from services.simulator.app import app


class SimulatorServiceTests(unittest.TestCase):
    def test_health_contract(self) -> None:
        response = TestClient(app).get("/api/simulator/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "simulator"})


if __name__ == "__main__":
    unittest.main()
