import unittest

from resolver.report_view_v3 import _build_planner_summary, _compute_planner_score


class TestReportViewV3Planning(unittest.TestCase):
    def test_compute_planner_score_prefers_low_risk(self) -> None:
        high = _compute_planner_score(
            {
                "modulesTotal": 10,
                "modulesReady": 6,
                "modulesNeedUpdate": 3,
                "modulesBlocked": 0,
                "modulesNeedsVerification": 0,
                "modulesManualUpdate": 0,
            }
        )
        low = _compute_planner_score(
            {
                "modulesTotal": 10,
                "modulesReady": 2,
                "modulesNeedUpdate": 2,
                "modulesBlocked": 3,
                "modulesNeedsVerification": 2,
                "modulesManualUpdate": 1,
            }
        )
        self.assertGreater(high["value"], low["value"])
        self.assertEqual(high["tone"], "green")
        self.assertIn(low["tone"], {"yellow", "red"})

    def test_build_planner_summary_selects_best_target(self) -> None:
        targets = [
            {"foundryVersion": "13.360", "score": {"value": 41.0, "tone": "yellow"}, "quickStatus": {"modulesReady": 3, "modulesNeedUpdate": 2, "modulesBlocked": 2, "modulesNeedsVerification": 1}},
            {"foundryVersion": "13.370", "score": {"value": 82.5, "tone": "green"}, "quickStatus": {"modulesReady": 7, "modulesNeedUpdate": 2, "modulesBlocked": 0, "modulesNeedsVerification": 0}},
        ]
        summary = _build_planner_summary(targets)
        self.assertEqual(summary["bestTargetVersion"], "13.370")
        self.assertGreater(summary["bestTargetScore"], 80.0)
        self.assertEqual(summary["bestTargetTone"], "green")
        self.assertIn("Best balance of coverage and risk", summary["bestTargetReason"])


if __name__ == "__main__":
    unittest.main()

