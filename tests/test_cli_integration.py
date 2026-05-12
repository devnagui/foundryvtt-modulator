import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from resolver import cli
from resolver.models import Recommendation
from tests.helpers import (
    MINIMAL_FOUNDRY_FIXTURE,
    clone_fixture_tree,
    real_foundry_data_root,
    should_run_real_foundry_tests,
)


class TestCliIntegration(unittest.TestCase):
    def test_main_generates_reports_and_json_payload(self) -> None:
        tmp_handle, data_root = clone_fixture_tree(MINIMAL_FOUNDRY_FIXTURE)
        with tmp_handle:
            root = data_root.parent

            report_json = root / "report.json"
            report_html = root / "report.html"
            report_log = root / "report.log"
            cache_dir = root / ".cache"
            db_path = root / "state" / "resolver.db"

            def fake_resolve(module, *_args, **_kwargs):
                return (
                    Recommendation(
                        module=module.module_id,
                        installed_version=module.version,
                        recommended_version=module.version,
                        reason="test recommendation",
                        confidence="high",
                        verified_version="13.300",
                        manifest_url=module.manifest_url,
                        download_url=None,
                        source="test",
                        checked_releases=1,
                    ),
                    {},
                )

            argv = [
                "resolver.cli",
                "--data-root",
                str(data_root),
                "--dry-run",
                "--batch-size",
                "10",
                "--cache-dir",
                str(cache_dir),
                "--database-path",
                str(db_path),
                "--json-output",
                str(report_json),
                "--html-report",
                str(report_html),
                "--log-file",
                str(report_log),
            ]

            with patch.object(sys, "argv", argv), \
                patch("resolver.cli.resolve_module_recommendation", side_effect=fake_resolve), \
                patch("resolver.cli.fetch_release_history", return_value=([], [])), \
                patch("resolver.cli.fetch_system_release_history", return_value=([], [])), \
                patch("resolver.cli.list_future_foundry_releases", return_value=[]), \
                patch("resolver.cli.render_report_html", return_value="<html>v3</html>"), \
                patch("resolver.cli._is_foundry_running", return_value=(False, "ok")):
                exit_code = cli.main()
            logging.shutdown()

            self.assertEqual(exit_code, 0)
            self.assertTrue(report_json.exists())
            self.assertTrue(report_html.exists())

            payload = json.loads(report_json.read_text(encoding="utf-8"))
            golden = json.loads((Path(__file__).resolve().parent / "golden" / "cli_expected_minimal.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["targetVersion"], golden["targetVersion"])
            self.assertEqual(payload["moduleCount"], golden["moduleCount"])
            self.assertEqual(payload["dryRun"], golden["dryRun"])
            self.assertEqual(sorted([item["module"] for item in payload["results"]]), sorted(golden["resultModules"]))
            self.assertIn("databaseSummary", payload)

    def test_main_without_html_flag_does_not_generate_html_report(self) -> None:
        tmp_handle, data_root = clone_fixture_tree(MINIMAL_FOUNDRY_FIXTURE)
        with tmp_handle:
            root = data_root.parent
            report_json = root / "report.json"
            report_log = root / "report.log"
            cache_dir = root / ".cache"
            db_path = root / "state" / "resolver.db"

            def fake_resolve(module, *_args, **_kwargs):
                return (
                    Recommendation(
                        module=module.module_id,
                        installed_version=module.version,
                        recommended_version=module.version,
                        reason="test recommendation",
                        confidence="high",
                        verified_version="13.300",
                        manifest_url=module.manifest_url,
                        download_url=None,
                        source="test",
                        checked_releases=1,
                    ),
                    {},
                )

            argv = [
                "resolver.cli",
                "--data-root",
                str(data_root),
                "--dry-run",
                "--batch-size",
                "10",
                "--cache-dir",
                str(cache_dir),
                "--database-path",
                str(db_path),
                "--json-output",
                str(report_json),
                "--log-file",
                str(report_log),
            ]

            with patch.object(sys, "argv", argv), patch(
                "resolver.cli.resolve_module_recommendation", side_effect=fake_resolve
            ), patch("resolver.cli.fetch_release_history", return_value=([], [])), patch(
                "resolver.cli.fetch_system_release_history", return_value=([], [])
            ), patch("resolver.cli.list_future_foundry_releases", return_value=[]), patch(
                "resolver.cli._is_foundry_running", return_value=(False, "ok")
            ), patch("resolver.cli.render_report_html") as mocked_render_html:
                exit_code = cli.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(report_json.exists())
            self.assertEqual(0, mocked_render_html.call_count)

    @unittest.skipUnless(should_run_real_foundry_tests(), "Set RUN_REAL_FOUNDRY_TESTS=1 to run against real Foundry.")
    def test_real_foundry_data_root_dry_run_smoke(self) -> None:
        data_root = real_foundry_data_root()
        if not data_root:
            self.skipTest("Set REAL_FOUNDRY_DATA_ROOT to your local Foundry data root.")
        root = Path(data_root).resolve().parent
        report_json = root / "real-smoke-report.json"
        report_html = root / "real-smoke-report.html"
        report_log = root / "real-smoke-report.log"
        cache_dir = root / ".cache-real-smoke"
        db_path = root / "state" / "resolver-real-smoke.db"

        argv = [
            "resolver.cli",
            "--data-root",
            str(data_root),
            "--dry-run",
            "--batch-size",
            "10",
            "--cache-dir",
            str(cache_dir),
            "--database-path",
            str(db_path),
            "--json-output",
            str(report_json),
            "--html-report",
            str(report_html),
            "--log-file",
            str(report_log),
            "--disable-blocked-refresh",
        ]

        with patch.object(sys, "argv", argv):
            exit_code = cli.main()
        self.assertEqual(exit_code, 0)
        self.assertTrue(report_json.exists())


if __name__ == "__main__":
    unittest.main()
