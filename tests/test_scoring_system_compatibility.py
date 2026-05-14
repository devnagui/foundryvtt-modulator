from __future__ import annotations

import unittest

from resolver.models import ReleaseRecord
from resolver.scoring import satisfies_release_constraints


class ScoringSystemCompatibilityTests(unittest.TestCase):
    def _release(self, system_compatibility: dict) -> ReleaseRecord:
        return ReleaseRecord(
            version="1.2.3",
            manifest_url=None,
            compatibility={"minimum": "13.0", "verified": "13.320", "maximum": "13.999"},
            system_compatibility=system_compatibility,
            module_requirements=[],
            download_url=None,
            source="test",
        )

    def test_missing_system_metadata_is_not_hard_block(self) -> None:
        rel = self._release({})
        ok = satisfies_release_constraints(rel, "13.350", {"dnd5e": "5.3.0"})
        self.assertTrue(ok)

    def test_missing_specific_system_entry_is_not_hard_block(self) -> None:
        rel = self._release({"pf2e": {"minimum": "6.0.0"}})
        ok = satisfies_release_constraints(rel, "13.350", {"dnd5e": "5.3.0"})
        self.assertTrue(ok)

    def test_explicit_system_bounds_still_block_when_outside(self) -> None:
        rel = self._release({"dnd5e": {"maximum": "5.2.9"}})
        ok = satisfies_release_constraints(rel, "13.350", {"dnd5e": "5.3.0"})
        self.assertFalse(ok)

    def test_system_compatibility_list_does_not_crash(self) -> None:
        rel = self._release({"dnd5e": [{"minimum": "5.0.0", "verified": "5.3.0", "maximum": "5.3.9"}]})
        ok = satisfies_release_constraints(rel, "13.350", {"dnd5e": "5.3.0"})
        self.assertTrue(ok)

    def test_release_compatibility_list_does_not_crash(self) -> None:
        rel = ReleaseRecord(
            version="1.2.3",
            manifest_url=None,
            compatibility=[{"minimum": "13.0", "verified": "13.320", "maximum": "13.999"}],
            system_compatibility={},
            module_requirements=[],
            download_url=None,
            source="test",
        )
        ok = satisfies_release_constraints(rel, "13.350", {"dnd5e": "5.3.0"})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
