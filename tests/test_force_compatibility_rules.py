from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from resolver.apply import force_module_compatibility
from resolver.models import ModuleRecord


class ForceCompatibilityRulesTests(unittest.TestCase):
    def _module_record(self, module_id: str, module_dir: Path, manifest: dict) -> ModuleRecord:
        return ModuleRecord(
            module_id=module_id,
            title=str(manifest.get("title") or module_id),
            version=str(manifest.get("version") or ""),
            manifest_url=str(manifest.get("manifest") or ""),
            project_url=str(manifest.get("url") or ""),
            path=str(module_dir),
            raw_manifest=manifest,
        )

    def test_force_updates_minimum_only_when_bounds_are_not_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir) / "Data" / "modules"
            module_dir = modules_dir / "dae"
            module_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = module_dir / "module.json"
            manifest = {
                "id": "dae",
                "title": "DAE",
                "version": "13.0.0",
                "compatibility": {"minimum": "14", "verified": "14.356", "maximum": "14.999"},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            module = self._module_record("dae", module_dir, manifest)

            result = force_module_compatibility(module, str(modules_dir), "13.361")

            updated = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("13.361", str((updated.get("compatibility") or {}).get("minimum") or ""))
            self.assertEqual("14.999", str((updated.get("compatibility") or {}).get("maximum") or ""))
            self.assertEqual("14.356", str((updated.get("compatibility") or {}).get("verified") or ""))
            self.assertIsNone(result.get("backupPath"))
            forced = (((updated.get("flags") or {}).get("resolver") or {}).get("forcedCompatibility") or {})
            self.assertTrue(bool(forced.get("enabled")))

    def test_force_updates_maximum_too_when_bounds_are_lockstep(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir) / "Data" / "modules"
            module_dir = modules_dir / "locked"
            module_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = module_dir / "module.json"
            manifest = {
                "id": "locked",
                "title": "Locked",
                "version": "1.0.0",
                "compatibility": {"minimum": "14", "verified": "14.356", "maximum": "14"},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            module = self._module_record("locked", module_dir, manifest)

            force_module_compatibility(module, str(modules_dir), "13.361")

            updated = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("13.361", str((updated.get("compatibility") or {}).get("minimum") or ""))
            self.assertEqual("13.361", str((updated.get("compatibility") or {}).get("maximum") or ""))
            self.assertEqual("14.356", str((updated.get("compatibility") or {}).get("verified") or ""))


if __name__ == "__main__":
    unittest.main()
