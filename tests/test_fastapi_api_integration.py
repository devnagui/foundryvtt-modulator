from __future__ import annotations

import os
import json
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
        ui_dist_dir = root / "frontend-dist"
        (data_root / "Data" / "modules").mkdir(parents=True, exist_ok=True)
        (data_root / "Data" / "worlds").mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        ui_dist_dir.mkdir(parents=True, exist_ok=True)
        (ui_dist_dir / "index.html").write_text(
            "<!doctype html><html><body><div id=\"root\"></div></body></html>",
            encoding="utf-8",
        )

        os.environ["RESOLVER_STATE_DIR"] = str(state_dir)
        os.environ["RESOLVER_REPORTS_DIR"] = str(reports_dir)
        os.environ["RESOLVER_DATA_ROOT"] = str(data_root)
        os.environ["RESOLVER_UI_DIST_DIR"] = str(ui_dist_dir)
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

    def test_report_model_includes_presentation_status_annotations(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        report_path = Path(os.environ["RESOLVER_REPORTS_DIR"]) / "module-resolver-latest.json"
        payload = {
            "generatedAt": "2026-05-12T00:00:00Z",
            "targetVersion": "13.350",
            "dataRoot": os.environ["RESOLVER_DATA_ROOT"],
            "installedSystemVersions": {"dnd5e": "5.3.0"},
            "reportViews": {
                "v3": {
                    "currentSystemUpgrades": {
                        "rows": [
                            {
                                "module": "dae",
                                "state": "update",
                                "reason": "missing_dependency:socketlib",
                                "missingDependencies": [{"module": "socketlib"}],
                            }
                        ]
                    }
                }
            },
            "results": [],
        }
        report_path.write_text(json.dumps(payload), encoding="utf-8")
        response = self.client.get("/api/v1/report/v3/model")
        self.assertEqual(200, response.status_code)
        rows = (((response.json().get("view") or {}).get("currentSystemUpgrades") or {}).get("rows") or [])
        self.assertEqual(1, len(rows))
        self.assertEqual("missing", rows[0].get("presentationStatus"))
        self.assertTrue(bool(rows[0].get("hasMissingDependencies")))

    def test_root_serves_react_shell(self) -> None:
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("id=\"root\"", response.text)

    def test_report_html_export_endpoint_generates_html_explicitly(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        report_path = Path(os.environ["RESOLVER_REPORTS_DIR"]) / "module-resolver-latest.json"
        report_path.write_text(
            json.dumps(
                {
                    "generatedAt": "2026-05-12T00:00:00Z",
                    "targetVersion": "13.350",
                    "dataRoot": os.environ["RESOLVER_DATA_ROOT"],
                    "installedSystemVersions": {"dnd5e": "5.3.0"},
                    "reportViews": {"v3": {"currentSystemUpgrades": {"rows": []}}},
                    "results": [],
                }
            ),
            encoding="utf-8",
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        response = self.client.post("/api/v1/report/v3/export-html", json={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(bool(payload.get("ok")))
        html_path = Path(payload.get("path") or "")
        self.assertTrue(html_path.exists())

    def test_report_snapshot_export_endpoint_generates_json(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        module_dir = Path(os.environ["RESOLVER_DATA_ROOT"]) / "Data" / "modules" / "snapmod"
        module_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = Path(os.environ["RESOLVER_DATA_ROOT"]) / "Logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "diagnostics.json").write_text(
            json.dumps({"foundry": {"generation": 13, "build": 351}}),
            encoding="utf-8",
        )
        (module_dir / "module.json").write_text(
            json.dumps(
                {
                    "id": "snapmod",
                    "title": "Snapshot Module",
                    "version": "1.2.3",
                    "compatibility": {"minimum": "13", "verified": "13.351", "maximum": "13.999"},
                }
            ),
            encoding="utf-8",
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        response = self.client.post("/api/v1/report/v3/export-snapshot", json={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(bool(payload.get("ok")))
        out = Path(payload.get("path") or "")
        self.assertTrue(out.exists())
        snapshot = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("modules", snapshot)
        self.assertTrue(any(str(row.get("module")) == "snapmod" for row in (snapshot.get("modules") or [])))

    def test_report_export_log_endpoint_returns_latest_log_content(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        reports_dir = Path(os.environ["RESOLVER_REPORTS_DIR"])
        log_path = reports_dir / "module-resolver-latest.log"
        log_path.write_text("line-1\nline-2\n", encoding="utf-8")
        csrf = self.client.cookies.get("mm_csrf") or ""
        response = self.client.post("/api/v1/report/v3/export-log", json={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(bool(payload.get("ok")))
        self.assertIn("line-1", str(payload.get("content") or ""))
        self.assertTrue(str(payload.get("fileName") or "").endswith(".log"))

    def test_report_update_artifacts_endpoints_list_detail_download(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        artifacts_dir = Path(os.environ["RESOLVER_STATE_DIR"]) / "update-artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "schemaVersion": "1.0.0",
            "planId": "plan-test-001",
            "createdAt": "2026-05-15T10:00:00Z",
            "action": "apply",
            "foundryCurrentVersion": "13.351",
            "foundryTargetVersion": "13.351",
            "systems": [{"name": "dnd5e", "currentVersion": "5.3.0", "targetVersion": "5.3.0"}],
            "modules": [{"name": "dae", "currentVersion": "12.0.0", "targetVersion": "13.0.0"}],
            "backups": [],
            "summary": {"applied": 1, "skipped": 0, "failed": 0},
        }
        (artifacts_dir / "plan-test-001.json").write_text(json.dumps(artifact), encoding="utf-8")

        list_resp = self.client.get("/api/v1/report/v3/update-artifacts?limit=10")
        self.assertEqual(200, list_resp.status_code)
        list_payload = list_resp.json()
        self.assertTrue(bool(list_payload.get("ok")))
        self.assertTrue(any(str(item.get("planId") or "") == "plan-test-001" for item in (list_payload.get("items") or [])))

        detail_resp = self.client.get("/api/v1/report/v3/update-artifacts/plan-test-001")
        self.assertEqual(200, detail_resp.status_code)
        detail_payload = detail_resp.json()
        self.assertTrue(bool(detail_payload.get("ok")))
        self.assertEqual("plan-test-001", str((detail_payload.get("artifact") or {}).get("planId") or ""))

        dl_resp = self.client.get("/api/v1/report/v3/update-artifacts/plan-test-001/download?includeBackupData=false")
        self.assertEqual(200, dl_resp.status_code)
        self.assertIn("application/zip", str(dl_resp.headers.get("content-type") or ""))
        self.assertGreater(len(dl_resp.content), 0)

    def test_submit_import_alias_queues_override_from_plan(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        payload = {"current": {"rows": []}, "destiny": {"rows": []}}
        response = self.client.post(
            "/api/v1/actions/submit",
            json={
                "action": "Override From Plan",
                "payload": {"planContent": json.dumps(payload), "profile": "current"},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(bool(body.get("ok")))
        self.assertEqual("override-from-plan", body.get("action"))

    def test_submit_without_action_but_plan_payload_infers_override(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        payload = {"current": {"rows": []}, "destiny": {"rows": []}}
        response = self.client.post(
            "/api/v1/actions/submit",
            json={"payload": {"planContent": json.dumps(payload), "profile": "current"}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(bool(body.get("ok")))
        self.assertEqual("override-from-plan", body.get("action"))

    def test_submit_unknown_action_with_plan_payload_infers_override(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        payload = {"current": {"rows": []}, "destiny": {"rows": []}}
        response = self.client.post(
            "/api/v1/actions/submit",
            json={"action": "override-from-file-picker", "payload": {"planContent": json.dumps(payload), "profile": "current"}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(bool(body.get("ok")))
        self.assertEqual("override-from-plan", body.get("action"))

    def test_legacy_api_submit_alias_path_accepts_import(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        csrf = self.client.cookies.get("mm_csrf") or ""
        payload = {"current": {"rows": []}, "destiny": {"rows": []}}
        response = self.client.post(
            "/api/actions/submit",
            json={"action": "import", "payload": {"planContent": json.dumps(payload), "profile": "current"}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(bool(body.get("ok")))
        self.assertEqual("override-from-plan", body.get("action"))

    def test_import_history_endpoint_reads_persisted_entries(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        history_path = Path(os.environ["RESOLVER_STATE_DIR"]) / "import-history.json"
        history_path.write_text(
            json.dumps(
                [
                    {
                        "generatedAt": "2026-05-13T10:00:00Z",
                        "action": "override-from-plan",
                        "profile": "current",
                        "appliedCount": 2,
                        "skippedCount": 1,
                        "failureCount": 0,
                        "planPath": "inline:test",
                    }
                ]
            ),
            encoding="utf-8",
        )
        response = self.client.get("/api/v1/report/v3/import-history?limit=10")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(bool(payload.get("ok")))
        items = payload.get("items") or []
        self.assertEqual(1, len(items))
        self.assertEqual("override-from-plan", str(items[0].get("action") or ""))
        self.assertEqual(2, int(items[0].get("appliedCount") or 0))

    def test_planning_context_endpoint_returns_ok_empty_without_scan(self) -> None:
        self.client.post(
            "/api/v1/auth/setup",
            json={"username": "tester.user", "password": "Strong!Pass123", "confirmPassword": "Strong!Pass123"},
        )
        response = self.client.get("/api/v1/report/v3/planning-context?foundryVersion=14.361&systemId=dnd5e&systemVersion=5.3.3")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(bool(payload.get("ok")))
        self.assertEqual(0, int(payload.get("count") or 0))


if __name__ == "__main__":
    unittest.main()
