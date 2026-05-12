from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _bootstrap_runtime_env(root: Path) -> None:
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


class SuggestionsPerformanceFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _bootstrap_runtime_env(self.root)

        from backend.app.services import runtime as runtime_mod

        runtime_mod._RUNTIME = None
        runtime_mod._WORKER_STARTED = False
        runtime_mod._REPORT_SUGGEST_CACHE.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_report_with_current_row(self, module_id: str) -> None:
        payload = {
            "generatedAt": "2026-05-12T00:00:00Z",
            "targetVersion": "13.350",
            "dataRoot": str(self.root / "foundry"),
            "installedSystemVersions": {"dnd5e": "5.3.0"},
            "reportViews": {
                "v3": {
                    "currentSystemUpgrades": {
                        "rows": [
                            {
                                "module": module_id,
                                "title": module_id.upper(),
                                "installedVersion": "-",
                                "recommendedVersion": "-",
                                "releaseUrl": "",
                                "reason": "missing dependency",
                            }
                        ]
                    },
                    "backupManagement": {"rows": []},
                }
            },
            "results": [],
        }
        report_path = self.root / "reports" / "module-resolver-latest.json"
        report_path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_report_with_missing_dependency_action(self, parent_module_id: str, dependency_module_id: str) -> None:
        payload = {
            "generatedAt": "2026-05-12T00:00:00Z",
            "targetVersion": "13.350",
            "dataRoot": str(self.root / "foundry"),
            "installedSystemVersions": {"dnd5e": "5.3.0"},
            "reportViews": {
                "v3": {
                    "currentSystemUpgrades": {"rows": []},
                    "backupManagement": {"rows": []},
                }
            },
            "results": [
                {
                    "module": parent_module_id,
                    "title": parent_module_id.upper(),
                    "installedVersion": "13.0.0",
                    "recommendedVersion": "13.0.1",
                    "dependencyActions": [
                        {
                            "module": dependency_module_id,
                            "installedVersion": "",
                            "recommendedVersion": "",
                            "reason": f"Missing dependency required by {parent_module_id}",
                            "manifestUrl": "",
                            "downloadUrl": "",
                            "compatibility": {},
                            "systemCompatibility": {},
                        }
                    ],
                }
            ],
        }
        report_path = self.root / "reports" / "module-resolver-latest.json"
        report_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_read_report_model_enriches_missing_recommendation(self) -> None:
        from backend.app.services.runtime import get_runtime, read_report_model

        self._write_report_with_current_row("dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="dae",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/13.0.10",
            "manifestUrl": "https://example.invalid/module.json",
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            model = read_report_model(runtime)

        rows = (((model.get("view") or {}).get("currentSystemUpgrades") or {}).get("rows") or [])
        self.assertEqual(1, len(rows))
        self.assertEqual("13.0.10", rows[0].get("recommendedVersion"))
        self.assertEqual("https://gitlab.com/tposney/dae/-/releases/13.0.10", rows[0].get("releaseUrl"))
        self.assertEqual("missing", rows[0].get("presentationStatus"))

    def test_read_report_model_reuses_context_cache(self) -> None:
        from backend.app.services.runtime import get_runtime, read_report_model

        self._write_report_with_current_row("dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="dae",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/13.0.10",
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ) as mocked:
            read_report_model(runtime)
            read_report_model(runtime)
        self.assertEqual(1, mocked.call_count)

    def test_read_report_model_matches_module_source_case_insensitive(self) -> None:
        from backend.app.services.runtime import get_runtime, read_report_model

        self._write_report_with_current_row("dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="DAE",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/13.0.10",
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            model = read_report_model(runtime)

        rows = (((model.get("view") or {}).get("currentSystemUpgrades") or {}).get("rows") or [])
        self.assertEqual("13.0.10", rows[0].get("recommendedVersion"))

    def test_enrich_latest_report_file_persists_recommendation_after_scan(self) -> None:
        from backend.app.services.runtime import _enrich_latest_report_file, get_runtime

        self._write_report_with_current_row("dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="dae",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/13.0.10",
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            _enrich_latest_report_file(runtime)

        report_path = self.root / "reports" / "module-resolver-latest.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        rows = ((((payload.get("reportViews") or {}).get("v3") or {}).get("currentSystemUpgrades") or {}).get("rows") or [])
        self.assertEqual("13.0.10", rows[0].get("recommendedVersion"))
        self.assertEqual("https://gitlab.com/tposney/dae/-/releases/13.0.10", rows[0].get("releaseUrl"))
        html_path = self.root / "reports" / "module-resolver-latest.html"
        self.assertFalse(html_path.exists())

    def test_export_latest_report_html_writes_html_only_on_explicit_export(self) -> None:
        from backend.app.services.runtime import export_latest_report_html, get_runtime

        self._write_report_with_current_row("dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="dae",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/13.0.10",
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            result = export_latest_report_html(runtime)

        html_path = Path(result.get("path") or "")
        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(html_path.exists())

    def test_enrich_latest_report_file_enriches_missing_dependency_action_with_source(self) -> None:
        from backend.app.services.runtime import _enrich_latest_report_file, get_runtime

        self._write_report_with_missing_dependency_action("midi-qol", "dae")
        runtime = get_runtime()
        runtime.module_source_store.upsert_source(
            module_id="dae",
            manifest_url="",
            project_url="https://gitlab.com/tposney/dae",
        )
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "manifestUrl": "https://gitlab.com/tposney/dae/-/raw/13.0.10/module.json",
            "downloadUrl": "https://gitlab.com/tposney/dae/-/archive/13.0.10/dae.zip",
            "compatibility": {"minimum": "13", "verified": "13.350", "maximum": "13.999"},
            "systemCompatibility": {"dnd5e": {"minimum": "5.3.0", "maximum": "5.3.99"}},
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            _enrich_latest_report_file(runtime)

        report_path = self.root / "reports" / "module-resolver-latest.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        dep = (((payload.get("results") or [])[0].get("dependencyActions") or [])[0])
        self.assertEqual("13.0.10", dep.get("recommendedVersion"))
        self.assertEqual("https://gitlab.com/tposney/dae/-/raw/13.0.10/module.json", dep.get("manifestUrl"))
        self.assertEqual("https://gitlab.com/tposney/dae/-/archive/13.0.10/dae.zip", dep.get("downloadUrl"))

    def test_enrich_latest_report_file_keeps_unknown_dependency_action_when_source_missing(self) -> None:
        from backend.app.services.runtime import _enrich_latest_report_file, get_runtime

        self._write_report_with_missing_dependency_action("midi-qol", "socketlib")
        runtime = get_runtime()
        _enrich_latest_report_file(runtime)

        report_path = self.root / "reports" / "module-resolver-latest.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        dep = (((payload.get("results") or [])[0].get("dependencyActions") or [])[0])
        self.assertEqual("", dep.get("recommendedVersion"))
        self.assertEqual("", dep.get("manifestUrl"))

    def test_save_module_source_triggers_latest_report_enrichment(self) -> None:
        from backend.app.services.runtime import get_runtime, save_module_source

        self._write_report_with_missing_dependency_action("midi-qol", "dae")
        runtime = get_runtime()
        suggestion = {
            "module": "dae",
            "recommendedVersion": "13.0.10",
            "manifestUrl": "https://gitlab.com/tposney/dae/-/raw/13.0.10/module.json",
            "downloadUrl": "https://gitlab.com/tposney/dae/-/archive/13.0.10/dae.zip",
            "compatibility": {"minimum": "13", "verified": "13.350", "maximum": "13.999"},
            "systemCompatibility": {"dnd5e": {"minimum": "5.3.0", "maximum": "5.3.99"}},
        }
        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module",
            return_value=suggestion,
        ), patch(
            "backend.app.services.runtime.detect_foundry_version",
            return_value=("13.350", "test"),
        ), patch(
            "backend.app.services.runtime.load_system_versions",
            return_value={"dnd5e": "5.3.0"},
        ), patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value=suggestion,
        ):
            save_module_source(
                runtime,
                module_id="dae",
                manifest_url="",
                project_url="https://gitlab.com/tposney/dae",
            )

        payload = json.loads((self.root / "reports" / "module-resolver-latest.json").read_text(encoding="utf-8"))
        dep = (((payload.get("results") or [])[0].get("dependencyActions") or [])[0])
        self.assertEqual("13.0.10", dep.get("recommendedVersion"))
        self.assertFalse((self.root / "reports" / "module-resolver-latest.html").exists())

    def test_suggest_modules_batch_route(self) -> None:
        from backend.app.main import create_app

        client = TestClient(create_app())
        setup = client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        self.assertEqual(201, setup.status_code)
        csrf = client.cookies.get("mm_csrf") or ""
        headers = {"X-CSRF-Token": csrf}
        with patch(
            "backend.app.api.routes.actions.suggest_modules_batch",
            return_value={
                "ok": True,
                "count": 2,
                "rows": [
                    {"moduleId": "dae", "suggestion": {"recommendedVersion": "13.0.10"}},
                    {"moduleId": "socketlib", "error": "manifest_or_project_required"},
                ],
            },
        ):
            response = client.post(
                "/api/v1/actions/suggest-modules-batch",
                json={
                    "modules": [
                        {"moduleId": "dae", "projectUrl": "https://gitlab.com/tposney/dae"},
                        {"moduleId": "socketlib"},
                    ],
                    "targetFoundryVersion": "13.350",
                    "installedSystemVersions": {"dnd5e": "5.3.0"},
                },
                headers=headers,
            )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(2, payload.get("count"))
        self.assertEqual("dae", (payload.get("rows") or [])[0].get("moduleId"))


if __name__ == "__main__":
    unittest.main()
