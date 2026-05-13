from __future__ import annotations

import unittest

from backend.app.services.runtime import _annotate_presentation_statuses, _index_planner_targets_by_foundry


class RuntimePresentationStatusTests(unittest.TestCase):
    def test_current_rows_missing_overrides_state(self) -> None:
        view = {
            "currentSystemUpgrades": {
                "rows": [
                    {
                        "module": "dae",
                        "state": "update",
                        "reason": "could not be resolved | missing_dependency:socketlib",
                        "missingDependencies": [{"module": "socketlib"}],
                    }
                ]
            }
        }
        _annotate_presentation_statuses(view)
        row = view["currentSystemUpgrades"]["rows"][0]
        self.assertEqual("missing", row.get("presentationStatus"))
        self.assertTrue(bool(row.get("hasMissingDependencies")))

    def test_current_rows_blocked_when_unknown_state_and_no_missing(self) -> None:
        view = {
            "currentSystemUpgrades": {
                "rows": [
                    {
                        "module": "midi-qol",
                        "state": "",
                        "reason": "incompatible with target",
                        "missingDependencies": [],
                    }
                ]
            }
        }
        _annotate_presentation_statuses(view)
        row = view["currentSystemUpgrades"]["rows"][0]
        self.assertEqual("blocked", row.get("presentationStatus"))
        self.assertFalse(bool(row.get("hasMissingDependencies")))

    def test_current_rows_missing_takes_priority_over_blocked_reason(self) -> None:
        view = {
            "currentSystemUpgrades": {
                "rows": [
                    {
                        "module": "dae",
                        "state": "blocked",
                        "reason": "explicit incompatible | missing_dependency:socketlib",
                        "missingDependencies": [],
                    }
                ]
            }
        }
        _annotate_presentation_statuses(view)
        row = view["currentSystemUpgrades"]["rows"][0]
        self.assertEqual("missing", row.get("presentationStatus"))
        self.assertTrue(bool(row.get("hasMissingDependencies")))

    def test_current_rows_update_and_ready_are_preserved_without_missing(self) -> None:
        view = {
            "currentSystemUpgrades": {
                "rows": [
                    {"module": "midi-qol", "state": "update", "reason": "upgrade available", "missingDependencies": []},
                    {"module": "socketlib", "state": "ready", "reason": "ok", "missingDependencies": []},
                ]
            }
        }
        _annotate_presentation_statuses(view)
        rows = view["currentSystemUpgrades"]["rows"]
        self.assertEqual("update", rows[0].get("presentationStatus"))
        self.assertEqual("ready", rows[1].get("presentationStatus"))
        self.assertFalse(bool(rows[0].get("hasMissingDependencies")))
        self.assertFalse(bool(rows[1].get("hasMissingDependencies")))

    def test_planner_bucket_status_mapping(self) -> None:
        view = {
            "systemUpgradePlanner": {
                "targets": [
                    {
                        "systemRows": [
                            {
                                "blockedModuleRows": [{"module": "a", "reason": "explicit incompatible"}],
                                "upgradableModuleRows": [{"module": "b", "reason": "upgrade available"}],
                                "compatibleModuleRows": [{"module": "c", "reason": "ok"}],
                                "unknownModuleRows": [{"module": "d", "reason": "unknown metadata"}],
                                "localManifestManualModules": [{"module": "e", "reason": "manual module"}],
                            }
                        ]
                    }
                ]
            }
        }
        _annotate_presentation_statuses(view)
        system = view["systemUpgradePlanner"]["targets"][0]["systemRows"][0]
        self.assertEqual("blocked", system["blockedModuleRows"][0].get("presentationStatus"))
        self.assertEqual("update", system["upgradableModuleRows"][0].get("presentationStatus"))
        self.assertEqual("ready", system["compatibleModuleRows"][0].get("presentationStatus"))
        self.assertEqual("blocked", system["unknownModuleRows"][0].get("presentationStatus"))
        self.assertEqual("blocked", system["localManifestManualModules"][0].get("presentationStatus"))

    def test_planner_missing_dependency_token_overrides_bucket(self) -> None:
        view = {
            "systemUpgradePlanner": {
                "targets": [
                    {
                        "systemRows": [
                            {
                                "compatibleModuleRows": [
                                    {"module": "dae", "reason": "ok | missing_dependency:socketlib"},
                                ]
                            }
                        ]
                    }
                ]
            }
        }
        _annotate_presentation_statuses(view)
        row = view["systemUpgradePlanner"]["targets"][0]["systemRows"][0]["compatibleModuleRows"][0]
        self.assertEqual("missing", row.get("presentationStatus"))
        self.assertTrue(bool(row.get("hasMissingDependencies")))

    def test_planner_targets_indexed_by_foundry_version(self) -> None:
        view = {
            "systemUpgradePlanner": {
                "targets": [
                    {"foundryVersion": "13.350", "systemRows": []},
                    {"foundryVersion": "14.0.2", "systemRows": []},
                ]
            }
        }
        _index_planner_targets_by_foundry(view)
        planner = view["systemUpgradePlanner"]
        indexed = planner.get("targetsByFoundry") or {}
        self.assertIn("13.350", indexed)
        self.assertIn("14.0.2", indexed)
        self.assertEqual("13.350", indexed["13.350"].get("foundryVersion"))
        self.assertEqual("14.0.2", indexed["14.0.2"].get("foundryVersion"))


if __name__ == "__main__":
    unittest.main()
