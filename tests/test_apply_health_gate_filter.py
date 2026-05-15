from __future__ import annotations

import unittest

from backend.app.services import runtime as runtime_mod


class ApplyHealthGateFilterTests(unittest.TestCase):
    def test_filters_blocked_modules_and_keeps_rest(self) -> None:
        selected = ["lib-wrapper", "dae", "midi-qol"]
        gate = {
            "blocked": True,
            "rows": [
                {"module": "lib-wrapper"},
                {"module": "DAE"},
            ],
        }
        allowed, skipped = runtime_mod._filter_apply_modules_by_health_gate(selected, gate)
        self.assertEqual(["midi-qol"], allowed)
        self.assertEqual(["lib-wrapper", "dae"], skipped)

    def test_keeps_all_when_no_blocked_rows(self) -> None:
        selected = ["a", "b", "c"]
        gate = {"blocked": False, "rows": []}
        allowed, skipped = runtime_mod._filter_apply_modules_by_health_gate(selected, gate)
        self.assertEqual(["a", "b", "c"], allowed)
        self.assertEqual([], skipped)

    def test_empty_selected_returns_empty(self) -> None:
        allowed, skipped = runtime_mod._filter_apply_modules_by_health_gate([], {"blocked": True, "rows": [{"module": "x"}]})
        self.assertEqual([], allowed)
        self.assertEqual([], skipped)


if __name__ == "__main__":
    unittest.main()

