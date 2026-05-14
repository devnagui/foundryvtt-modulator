from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import os

from resolver.db import persist_scan_snapshot
from resolver.models import ModuleRecord, ModuleRelationship, ReleaseRecord


class DbPersistCompatShapesTests(unittest.TestCase):
    def test_persist_snapshot_accepts_list_compat_shapes(self) -> None:
        tmp = tempfile.mkdtemp(prefix="resolver-db-shapes-")
        db_path = str(Path(tmp) / "resolver.db")
        try:
            payload = {
                "generatedAt": "2026-05-13T00:00:00Z",
                "targetVersion": "13.351",
                "dataRoot": "D:/foundry",
                "dryRun": True,
                "apply": False,
                "results": [],
                "futureUpgradeMatrix": [],
            }
            installed_modules = [
                ModuleRecord(
                    module_id="dae",
                    title="DAE",
                    version="13.0.26",
                    manifest_url=None,
                    project_url="https://gitlab.com/tposney/dae",
                    path="D:/foundry/Data/modules/dae",
                    raw_manifest={"id": "dae", "version": "13.0.26"},
                )
            ]
            histories = {
                "dae": (
                    5,
                    [
                        ReleaseRecord(
                            version="13.0.27",
                            manifest_url=None,
                            compatibility=[{"minimum": "13", "verified": "13.351", "maximum": "13.999"}],  # type: ignore[arg-type]
                            system_compatibility={"dnd5e": [{"minimum": "5.3.0", "verified": "5.3.0", "maximum": "5.3.9"}]},  # type: ignore[arg-type]
                            module_requirements=[
                                ModuleRelationship(
                                    module_id="socketlib",
                                    type="requires",
                                    compatibility=[{"minimum": "1.0.0", "verified": "1.1.4", "maximum": "1.999"}],  # type: ignore[arg-type]
                                )
                            ],
                            download_url=None,
                            source="test",
                            raw_manifest={"id": "dae", "version": "13.0.27"},
                        )
                    ],
                    [],
                )
            }

            scan_run_id = persist_scan_snapshot(
                database_path=db_path,
                payload=payload,
                installed_modules=installed_modules,
                installed_systems=[],
                world_usage=[],
                module_histories=histories,
                system_histories={},
            )
            self.assertGreater(scan_run_id, 0)
        finally:
            try:
                if Path(db_path).exists():
                    Path(db_path).unlink()
            except OSError:
                pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
