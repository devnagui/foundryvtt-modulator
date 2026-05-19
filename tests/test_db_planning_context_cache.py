from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from resolver.db import persist_scan_snapshot
from resolver.db_queries import load_planning_context_rows
from resolver.models import ModuleRecord


class DbPlanningContextCacheTests(unittest.TestCase):
    def test_persist_and_query_planning_context_rows(self) -> None:
        tmp = tempfile.mkdtemp(prefix="resolver-db-planning-context-")
        db_path = str(Path(tmp) / "resolver.db")
        try:
            payload = {
                "generatedAt": "2026-05-14T00:00:00Z",
                "targetVersion": "14.361",
                "dataRoot": "D:/foundry",
                "dryRun": True,
                "apply": False,
                "results": [
                    {
                        "module": "dae",
                        "title": "DAE",
                        "installedVersion": "13.0.26",
                        "recommendedVersion": "13.0.30",
                        "reason": "missing_dependency:socketlib",
                        "compatibility": {"minimum": "13", "verified": "14.356", "maximum": "14.999"},
                        "systemCompatibility": {"dnd5e": {"minimum": "5.3.0", "verified": "5.3.3", "maximum": "5.3.9"}},
                    }
                ],
                "futureUpgradeMatrix": [
                    {
                        "targetFoundryVersion": "14.361",
                        "systems": [
                            {
                                "systemId": "dnd5e",
                                "recommendedVersion": "5.3.3",
                                "installedVersion": "5.3.3",
                            }
                        ],
                        "moduleOutcomes": [
                            {
                                "module": "dae",
                                "title": "DAE",
                                "installedVersion": "13.0.26",
                                "recommendedVersion": "13.0.30",
                                "status": "blocked",
                                "reason": "missing_dependency:socketlib",
                                "compatibility": {"minimum": "13", "verified": "14.356", "maximum": "14.999"},
                                "systemCompatibility": {"dnd5e": {"minimum": "5.3.0", "verified": "5.3.3", "maximum": "5.3.9"}},
                            }
                        ],
                    }
                ],
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
            scan_run_id = persist_scan_snapshot(
                database_path=db_path,
                payload=payload,
                installed_modules=installed_modules,
                installed_systems=[],
                world_usage=[],
                module_histories={},
                system_histories={},
            )
            self.assertGreater(scan_run_id, 0)
            result = load_planning_context_rows(
                database_path=db_path,
                foundry_version="14.361",
                system_id="dnd5e",
                system_version="5.3.3",
            )
            self.assertTrue(bool(result.get("ok")))
            rows = result.get("rows") or []
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("dae", str(row.get("moduleId") or ""))
            self.assertEqual("missing", str(row.get("status") or ""))
            self.assertTrue(bool(row.get("hasMissingDependencies")))
        finally:
            try:
                if Path(db_path).exists():
                    Path(db_path).unlink()
            except OSError:
                pass

    def test_context_counts_match_partition_invariants(self) -> None:
        tmp = tempfile.mkdtemp(prefix="resolver-db-planning-context-partition-")
        db_path = str(Path(tmp) / "resolver.db")
        try:
            payload = {
                "generatedAt": "2026-05-14T00:00:00Z",
                "targetVersion": "14.361",
                "dataRoot": "D:/foundry",
                "dryRun": True,
                "apply": False,
                "results": [
                    {"module": "m3", "title": "M3", "installedVersion": "1.0", "recommendedVersion": "1.0", "reason": "ok", "compatibility": {"minimum": "14", "verified": "14", "maximum": "14"}, "systemCompatibility": {}},
                    {"module": "m4", "title": "M4", "installedVersion": "1.0", "recommendedVersion": "1.0", "reason": "unknown", "compatibility": {"minimum": "12", "maximum": "13"}, "systemCompatibility": {}},
                ],
                "futureUpgradeMatrix": [
                    {
                        "targetFoundryVersion": "14.361",
                        "systems": [
                            {
                                "systemId": "dnd5e",
                                "recommendedVersion": "5.3.3",
                                "installedVersion": "5.3.3",
                            }
                        ],
                        "moduleOutcomes": [
                            {"module": "m1", "title": "M1", "installedVersion": "1.0", "recommendedVersion": "1.0", "status": "blocked", "reason": "incompatible", "compatibility": {}, "systemCompatibility": {}},
                            {"module": "m2", "title": "M2", "installedVersion": "1.0", "recommendedVersion": "1.1", "status": "update", "reason": "upgrade", "compatibility": {}, "systemCompatibility": {}},
                        ],
                    }
                ],
            }
            persist_scan_snapshot(
                database_path=db_path,
                payload=payload,
                installed_modules=[],
                installed_systems=[],
                world_usage=[],
                module_histories={},
                system_histories={},
            )
            result = load_planning_context_rows(
                database_path=db_path,
                foundry_version="14.361",
                system_id="dnd5e",
                system_version="5.3.3",
            )
            rows = result.get("rows") or []
            blocked = len([row for row in rows if str(row.get("status") or "") in {"blocked", "missing"}])
            update = len([row for row in rows if str(row.get("status") or "") == "update"])
            ready = len([row for row in rows if str(row.get("status") or "") == "ready"])
            self.assertEqual(len(rows), blocked + update + ready)
            readiness_pct = round(((ready + update) / len(rows)) * 100) if rows else 0
            self.assertEqual(50, readiness_pct)
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
            try:
                os.rmdir(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
