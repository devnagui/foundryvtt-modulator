from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.services.runtime import SuggestionProviderError


class SuggestApiTests(unittest.TestCase):
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
        setup = self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        assert setup.status_code == 201
        self.csrf = self.client.cookies.get("mm_csrf") or ""

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_suggest_module_returns_structured_provider_error(self) -> None:
        payload = {
            "errorCode": "provider_rate_limited",
            "message": "GitHub rate limit reached while loading versions.",
            "hint": "Retry in a few minutes or configure authenticated access/token.",
            "retryable": True,
            "raw": "HTTP Error 403: rate limit",
            "moduleId": "dae",
        }
        with patch("backend.app.api.routes.actions.suggest_module", side_effect=SuggestionProviderError(payload)):
            response = self.client.post(
                "/api/v1/actions/suggest-module",
                json={"moduleId": "dae", "projectUrl": "https://github.com/tposney/dae"},
                headers={"X-CSRF-Token": self.csrf},
            )
        self.assertEqual(400, response.status_code)
        body = response.json() or {}
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
        self.assertEqual("provider_rate_limited", str(detail.get("error") or ""))
        self.assertEqual("dae", str(detail.get("moduleId") or ""))
        self.assertTrue(bool(detail.get("retryable")))
        self.assertIn("Retry", str(detail.get("hint") or ""))

    def test_suggest_module_passes_force_refresh_to_runtime(self) -> None:
        captured: dict[str, object] = {}

        def _fake_suggest(_runtime, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "suggestion": {"recommendedVersion": "13.0.0"}}

        with patch("backend.app.api.routes.actions.suggest_module", side_effect=_fake_suggest):
            response = self.client.post(
                "/api/v1/actions/suggest-module",
                json={
                    "moduleId": "dae",
                    "projectUrl": "https://github.com/tposney/dae",
                    "forceRefresh": True,
                    "targetFoundryVersion": "14.361",
                    "installedSystemVersions": {"dnd5e": "5.3.3"},
                },
                headers={"X-CSRF-Token": self.csrf},
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual("dae", str(captured.get("module_id") or ""))
        self.assertEqual("https://github.com/tposney/dae", str(captured.get("project_url") or ""))
        self.assertTrue(bool(captured.get("force_refresh")))
        self.assertEqual("14.361", str(captured.get("target_foundry_version") or ""))
        self.assertEqual({"dnd5e": "5.3.3"}, captured.get("installed_system_versions_override"))

    def test_suggest_batch_passes_force_refresh_to_runtime(self) -> None:
        captured: dict[str, object] = {}

        def _fake_batch(_runtime, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "count": 1, "rows": [{"moduleId": "dae", "suggestion": {"recommendedVersion": "13.0.0"}}]}

        with patch("backend.app.api.routes.actions.suggest_modules_batch", side_effect=_fake_batch):
            response = self.client.post(
                "/api/v1/actions/suggest-modules-batch",
                json={
                    "modules": [{"moduleId": "dae", "projectUrl": "https://github.com/tposney/dae"}],
                    "forceRefresh": True,
                    "targetFoundryVersion": "14.361",
                    "installedSystemVersions": {"dnd5e": "5.3.3"},
                },
                headers={"X-CSRF-Token": self.csrf},
            )
        self.assertEqual(200, response.status_code)
        self.assertTrue(bool(captured.get("force_refresh")))
        self.assertEqual("14.361", str(captured.get("target_foundry_version") or ""))
        self.assertEqual({"dnd5e": "5.3.3"}, captured.get("installed_system_versions_override"))


if __name__ == "__main__":
    unittest.main()
