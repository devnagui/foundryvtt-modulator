from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.runtime import _build_override_plan_targets, _extract_plan_targets
from backend.app.services.runtime import _candidate_from_target
from backend.app.services.runtime import _canonical_action_name


class OverridePlanTargetsTests(unittest.TestCase):
    def test_canonical_action_name_accepts_import_aliases(self) -> None:
        self.assertEqual("override-from-plan", _canonical_action_name("override-from-plan"))
        self.assertEqual("override-from-plan", _canonical_action_name("override_from_plan"))
        self.assertEqual("override-from-plan", _canonical_action_name("override-plan"))
        self.assertEqual("override-from-plan", _canonical_action_name("import-plan"))
        self.assertEqual("override-from-plan", _canonical_action_name("import"))
        self.assertEqual("override-from-plan", _canonical_action_name("overrideFromPlan"))
        self.assertEqual("override-from-plan", _canonical_action_name("Override From Plan"))

    def test_extract_plan_targets_current_prefers_installed_version(self) -> None:
        payload = {
            "current": {
                "rows": [
                    {
                        "kind": "module",
                        "moduleId": "midi-qol",
                        "installedVersion": "13.0.61",
                        "recommendedVersion": "13.0.62",
                        "releaseUrl": "https://github.com/example/midi-qol/releases/tag/13.0.62",
                    }
                ]
            }
        }
        modules, systems = _extract_plan_targets(payload, "current")
        self.assertEqual({}, systems)
        self.assertIn("midi-qol", modules)
        self.assertEqual("13.0.61", modules["midi-qol"]["targetVersion"])

    def test_extract_plan_targets_destiny_prefers_recommended_version(self) -> None:
        payload = {
            "destiny": {
                "rows": [
                    {
                        "kind": "module",
                        "moduleId": "dae",
                        "installedVersion": "13.0.27",
                        "recommendedVersion": "14.0.9",
                        "releaseUrl": "https://gitlab.com/tposney/dae/-/releases/v14.0.9",
                    },
                    {
                        "kind": "system",
                        "systemId": "dnd5e",
                        "installedVersion": "5.3.0",
                        "targetVersion": "5.3.3",
                        "targetUrl": "https://github.com/foundryvtt/dnd5e/releases/tag/5.3.3",
                    },
                ]
            }
        }
        modules, systems = _extract_plan_targets(payload, "destiny")
        self.assertEqual("14.0.9", modules["dae"]["targetVersion"])
        self.assertEqual("5.3.3", systems["dnd5e"]["targetVersion"])

    def test_extract_plan_targets_current_adds_active_system_from_section(self) -> None:
        payload = {
            "current": {
                "activeSystemId": "dnd5e",
                "selectedSystemVersion": "5.3.0",
                "rows": [],
            }
        }
        _modules, systems = _extract_plan_targets(payload, "current")
        self.assertEqual("5.3.0", systems["dnd5e"]["targetVersion"])

    def test_extract_plan_targets_both_prefers_higher_version(self) -> None:
        payload = {
            "current": {
                "rows": [
                    {"kind": "module", "moduleId": "chris-premades", "installedVersion": "1.5.20", "recommendedVersion": "1.5.30"},
                ]
            },
            "destiny": {
                "rows": [
                    {"kind": "module", "moduleId": "chris-premades", "installedVersion": "1.5.20", "recommendedVersion": "1.5.40"},
                ]
            },
        }
        modules, _systems = _extract_plan_targets(payload, "both")
        self.assertEqual("1.5.40", modules["chris-premades"]["targetVersion"])

    def test_build_override_targets_uses_snapshot_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "snapshot.json"
            snapshot_payload = {
                "systems": {"dnd5e": "5.3.3"},
                "modules": [
                    {
                        "module": "dae",
                        "title": "DAE",
                        "version": "14.0.9",
                        "manifestUrl": "https://gitlab.com/tposney/dae/-/raw/v14.0.9/package/module.json",
                        "projectUrl": "https://gitlab.com/tposney/dae",
                    }
                ],
            }
            snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
            plan_payload = {
                "snapshot": {"path": str(snapshot_path)},
                "current": {"rows": []},
            }
            modules, systems, warnings = _build_override_plan_targets(plan_payload, "current")
            self.assertEqual([], warnings)
            self.assertEqual("14.0.9", modules["dae"]["targetVersion"])
            self.assertEqual("5.3.3", systems["dnd5e"]["targetVersion"])

    def test_build_override_targets_uses_inline_snapshot_data(self) -> None:
        plan_payload = {
            "snapshot": {
                "data": {
                    "systems": {"dnd5e": "5.3.3"},
                    "modules": [
                        {
                            "module": "dae",
                            "title": "DAE",
                            "version": "14.0.9",
                            "manifestUrl": "https://gitlab.com/tposney/dae/-/raw/v14.0.9/package/module.json",
                            "projectUrl": "https://gitlab.com/tposney/dae",
                        }
                    ],
                }
            },
            "current": {"rows": []},
        }
        modules, systems, warnings = _build_override_plan_targets(plan_payload, "current")
        self.assertEqual([], warnings)
        self.assertEqual("14.0.9", modules["dae"]["targetVersion"])
        self.assertEqual("5.3.3", systems["dnd5e"]["targetVersion"])

    def test_candidate_from_target_normalizes_release_url_to_project(self) -> None:
        module = _candidate_from_target(
            "dae",
            "dae",
            "0.0.0",
            "https://gitlab.com/tposney/dae/-/releases/v14.0.9",
        )
        self.assertEqual("https://gitlab.com/tposney/dae", module.project_url)


if __name__ == "__main__":
    unittest.main()
