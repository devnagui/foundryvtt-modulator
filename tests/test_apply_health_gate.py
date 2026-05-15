from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.services import runtime as runtime_mod


class ApplyHealthGateTests(unittest.TestCase):
    def test_missing_file_issue_is_non_blocking(self) -> None:
        fake_health = {
            "ok": True,
            "rows": [
                {
                    "module": "lib-wrapper",
                    "title": "libWrapper",
                    "issues": ["missing_file:dist/lib-wrapper.css"],
                    "warnings": [],
                }
            ],
        }
        with patch.object(runtime_mod, "_run_module_health_check", return_value=fake_health):
            gate = runtime_mod._evaluate_apply_health_gate("D:/fake-root", ["lib-wrapper"])
        self.assertTrue(gate["ok"])
        self.assertFalse(gate["blocked"])
        self.assertEqual("module_health_gate_ok", gate["reason"])

    def test_missing_dependency_warning_is_blocking(self) -> None:
        fake_health = {
            "ok": True,
            "rows": [
                {
                    "module": "dae",
                    "title": "Dynamic Active Effects",
                    "issues": [],
                    "warnings": ["missing_dependency:socketlib"],
                }
            ],
        }
        with patch.object(runtime_mod, "_run_module_health_check", return_value=fake_health):
            gate = runtime_mod._evaluate_apply_health_gate("D:/fake-root", ["dae"])
        self.assertTrue(gate["ok"])
        self.assertTrue(gate["blocked"])
        self.assertEqual("module_health_gate_failed", gate["reason"])
        self.assertEqual(1, len(gate["rows"]))

    def test_manifest_read_error_is_blocking(self) -> None:
        fake_health = {
            "ok": True,
            "rows": [
                {
                    "module": "broken-manifest",
                    "title": "Broken Manifest",
                    "issues": ["manifest_read_error:invalid json"],
                    "warnings": [],
                }
            ],
        }
        with patch.object(runtime_mod, "_run_module_health_check", return_value=fake_health):
            gate = runtime_mod._evaluate_apply_health_gate("D:/fake-root", ["broken-manifest"])
        self.assertTrue(gate["ok"])
        self.assertTrue(gate["blocked"])
        self.assertEqual("module_health_gate_failed", gate["reason"])


if __name__ == "__main__":
    unittest.main()

