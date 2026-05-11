from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app.services.runtime import execute_rollback, get_runtime


class RollbackExecutionTests(unittest.TestCase):
    def test_execute_rollback_restores_module_from_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "foundry"
            modules_root = data_root / "Data" / "modules"
            worlds_root = data_root / "Data" / "worlds"
            modules_root.mkdir(parents=True, exist_ok=True)
            worlds_root.mkdir(parents=True, exist_ok=True)

            module_dir = modules_root / "sample-module"
            module_dir.mkdir(parents=True, exist_ok=True)
            (module_dir / "module.json").write_text(
                json.dumps({"id": "sample-module", "version": "9.9.9"}) + "\n",
                encoding="utf-8",
            )

            backup_dir = data_root / "Backups" / "modules" / "sample-module"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / "module.sample-module.2026-05-12.123.bak"
            with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("module.json", json.dumps({"id": "sample-module", "version": "1.2.3"}))

            # Configure runtime
            import os
            from backend.app.services import runtime as runtime_mod

            os.environ["RESOLVER_DATA_ROOT"] = str(data_root)
            os.environ["RESOLVER_STATE_DIR"] = str(root / "state")
            os.environ["RESOLVER_REPORTS_DIR"] = str(root / "reports")
            os.environ["RESOLVER_REQUIRE_FOUNDRY_OFFLINE"] = "false"
            runtime_mod._RUNTIME = None
            runtime_mod._WORKER_STARTED = False
            runtime = get_runtime()

            with patch("backend.app.services.runtime.rollback_plan", return_value={"backupPaths": [str(backup_path)]}):
                result = execute_rollback(runtime, 42)

            self.assertTrue(result.get("ok"))
            self.assertEqual(1, int(result.get("restoredCount") or 0))
            restored_manifest = json.loads((module_dir / "module.json").read_text(encoding="utf-8"))
            self.assertEqual("1.2.3", restored_manifest.get("version"))


if __name__ == "__main__":
    unittest.main()
