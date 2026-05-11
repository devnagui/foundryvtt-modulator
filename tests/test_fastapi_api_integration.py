from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class FastApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        state_dir = root / "state"
        reports_dir = root / "reports"
        data_root = root / "foundry"
        (data_root / "Data" / "modules").mkdir(parents=True, exist_ok=True)
        (data_root / "Data" / "worlds").mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        os.environ["RESOLVER_STATE_DIR"] = str(state_dir)
        os.environ["RESOLVER_REPORTS_DIR"] = str(reports_dir)
        os.environ["RESOLVER_DATA_ROOT"] = str(data_root)
        os.environ["RESOLVER_COOKIE_SECURE"] = "false"
        os.environ["RESOLVER_REQUIRE_FOUNDRY_OFFLINE"] = "false"

        from backend.app.services import runtime as runtime_mod

        runtime_mod._RUNTIME = None
        runtime_mod._WORKER_STARTED = False

        from backend.app.main import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_and_auth_flow(self) -> None:
        health = self.client.get("/api/v1/health")
        self.assertEqual(200, health.status_code)
        self.assertTrue(health.json().get("ok"))

        setup = self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        self.assertEqual(201, setup.status_code)
        self.assertTrue(setup.json().get("ok"))

        status = self.client.get("/api/v1/auth/status")
        self.assertEqual(200, status.status_code)
        self.assertTrue(status.json().get("authenticated"))

    def test_report_model_missing_returns_first_run_flag(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        response = self.client.get("/api/v1/report/v3/model")
        self.assertEqual(404, response.status_code)
        self.assertEqual("latest_report_not_found", response.json().get("error"))
        self.assertTrue(bool(response.json().get("firstRunRequired")))


if __name__ == "__main__":
    unittest.main()
