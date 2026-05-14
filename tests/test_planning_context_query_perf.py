from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from resolver.db import persist_scan_snapshot
from resolver.db_queries import load_planning_context_rows
from resolver.models import ModuleRecord


class PlanningContextQueryPerfTests(unittest.TestCase):
    def test_warm_query_under_300ms_for_moderate_dataset(self) -> None:
        tmp = tempfile.mkdtemp(prefix="resolver-planning-perf-")
        db_path = str(Path(tmp) / "resolver.db")
        try:
            module_rows = []
            for i in range(1200):
                module_rows.append(
                    {
                        "module": f"mod-{i}",
                        "title": f"Module {i}",
                        "installedVersion": "1.0.0",
                        "recommendedVersion": "1.0.1",
                        "reason": "upgrade available",
                        "compatibility": {"minimum": "14", "verified": "14.361", "maximum": "14.999"},
                        "systemCompatibility": {"dnd5e": {"minimum": "5.3.0", "verified": "5.3.3", "maximum": "5.3.9"}},
                    }
                )
            payload = {
                "generatedAt": "2026-05-14T00:00:00Z",
                "targetVersion": "14.361",
                "dataRoot": "D:/foundry",
                "dryRun": True,
                "apply": False,
                "results": [],
                "futureUpgradeMatrix": [],
                "reportViews": {
                    "v3": {
                        "systemUpgradePlanner": {
                            "targets": [
                                {
                                    "foundryVersion": "14.361",
                                    "systemRows": [
                                        {
                                            "systemId": "dnd5e",
                                            "targetVersion": "5.3.3",
                                            "blockedModuleRows": [],
                                            "upgradableModuleRows": module_rows,
                                            "compatibleModuleRows": [],
                                            "unknownModuleRows": [],
                                            "localManifestManualModules": [],
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                },
            }
            installed_modules = [
                ModuleRecord(
                    module_id="mod-base",
                    title="Base",
                    version="1.0.0",
                    manifest_url=None,
                    project_url="",
                    path="D:/foundry/Data/modules/mod-base",
                    raw_manifest={"id": "mod-base", "version": "1.0.0"},
                )
            ]
            persist_scan_snapshot(
                database_path=db_path,
                payload=payload,
                installed_modules=installed_modules,
                installed_systems=[],
                world_usage=[],
                module_histories={},
                system_histories={},
            )
            _cold = load_planning_context_rows(
                database_path=db_path,
                foundry_version="14.361",
                system_id="dnd5e",
                system_version="5.3.3",
                limit=5000,
            )
            warm = load_planning_context_rows(
                database_path=db_path,
                foundry_version="14.361",
                system_id="dnd5e",
                system_version="5.3.3",
                limit=5000,
            )
            self.assertEqual(1200, int(warm.get("count") or 0))
            self.assertLess(float(warm.get("queryMs") or 9999.0), 300.0)
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
