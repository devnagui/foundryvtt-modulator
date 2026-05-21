from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from backend.app.services import runtime as runtime_mod


class ApplyHealthGateExecuteTests(unittest.TestCase):
    def test_apply_with_only_blocked_modules_returns_successful_noop(self) -> None:
        runtime = SimpleNamespace(
            config_store=SimpleNamespace(get_data_root=lambda: "D:/fake-root"),
            config=SimpleNamespace(
                data_root="D:/fake-root",
                python_bin="python",
                cache_dir="cache",
                state_dir=SimpleNamespace(__str__=lambda self: "state"),
                reports_dir=SimpleNamespace(__str__=lambda self: "reports"),
                tool_root=".",
            ),
            lock_store=SimpleNamespace(
                acquire=lambda **kwargs: {"acquired": True, "action": kwargs.get("action")},
                release=lambda: None,
            ),
        )
        payload = {"modules": ["lib-wrapper"]}
        gate = {"ok": True, "blocked": True, "rows": [{"module": "lib-wrapper"}]}

        with patch.object(runtime_mod, "_evaluate_apply_health_gate", return_value=gate), patch.object(
            runtime_mod, "_build_cli_args_from_action"
        ) as build_args:
            out = runtime_mod._execute_action_job(runtime, "apply", payload)

        self.assertTrue(out.get("ok"))
        self.assertEqual(0, int(out.get("returnCode", -1)))
        self.assertEqual(["lib-wrapper"], out.get("skippedModules"))
        self.assertIn("preflight", out)
        build_args.assert_not_called()

    def test_apply_without_modules_does_not_preflight_block(self) -> None:
        runtime = SimpleNamespace(
            config_store=SimpleNamespace(get_data_root=lambda: "D:/fake-root"),
            config=SimpleNamespace(
                data_root="D:/fake-root",
                python_bin="python",
                cache_dir="cache",
                state_dir=Path("state"),
                reports_dir=Path("reports"),
                tool_root=".",
            ),
            lock_store=SimpleNamespace(
                acquire=lambda **kwargs: {"acquired": True, "action": kwargs.get("action")},
                release=lambda: None,
            ),
        )

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        class _FakePopen:
            returncode = 0
            def __init__(self, *a, **kw):
                import io
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
            def wait(self):
                pass

        with patch.object(runtime_mod, "_evaluate_apply_health_gate", side_effect=AssertionError("should not call preflight gate when modules are empty")), \
            patch.object(runtime_mod, "subprocess") as subprocess_mod, \
            patch.object(runtime_mod, "_enrich_latest_report_file", return_value=None):
            subprocess_mod.Popen.side_effect = _FakePopen
            out = runtime_mod._execute_action_job(runtime, "apply", {})

        self.assertTrue(out.get("ok"))
        self.assertEqual(0, int(out.get("returnCode", -1)))


if __name__ == "__main__":
    unittest.main()
