import unittest

from resolver.dependencies import resolve_module_recommendation
from resolver.models import ModuleRecord, ModuleRelationship, Recommendation, ReleaseRecord


class TestDependencyResolution(unittest.TestCase):
    def _module(self, module_id: str, version: str = "1.0.0") -> ModuleRecord:
        return ModuleRecord(
            module_id=module_id,
            title=module_id,
            version=version,
            manifest_url=None,
            project_url=None,
            path="",
            raw_manifest={"id": module_id, "version": version, "compatibility": {"verified": "13.300"}},
        )

    def test_prefers_compatible_release_and_resolves_dependency_upgrade(self) -> None:
        parent = self._module("parent", "1.0.0")
        child = self._module("child", "1.0.0")

        parent_release = ReleaseRecord(
            version="2.0.0",
            manifest_url=None,
            compatibility={"minimum": "13.0", "verified": "13.320", "maximum": "13.999"},
            system_compatibility={},
            module_requirements=[
                ModuleRelationship(module_id="child", type="module", compatibility={"minimum": "1.1.0"}),
            ],
            download_url="https://example.invalid/parent.zip",
            source="test",
        )
        child_release = ReleaseRecord(
            version="1.2.0",
            manifest_url=None,
            compatibility={"minimum": "13.0", "verified": "13.320", "maximum": "13.999"},
            system_compatibility={},
            module_requirements=[],
            download_url="https://example.invalid/child.zip",
            source="test",
        )

        def fetch_history(module: ModuleRecord, _: int):
            if module.module_id == "parent":
                return [parent_release], []
            return [child_release], []

        def load_relationship(rel: ModuleRelationship):
            return child if rel.module_id == "child" else None

        recommendation, warnings = resolve_module_recommendation(
            parent,
            "13.350",
            {},
            fetch_history,
            load_relationship,
            {},
        )

        self.assertEqual(recommendation.recommended_version, "2.0.0")
        self.assertEqual(len(recommendation.dependency_updates), 1)
        self.assertEqual(recommendation.dependency_updates[0].module, "child")
        self.assertFalse(warnings)

    def test_detects_dependency_cycle_fallback(self) -> None:
        a = self._module("a", "1.0.0")
        b = self._module("b", "1.0.0")

        rel_a = ModuleRelationship(module_id="b", type="module", compatibility={"minimum": "1.0.0"})
        rel_b = ModuleRelationship(module_id="a", type="module", compatibility={"minimum": "1.0.0"})

        release_a = ReleaseRecord(
            version="1.1.0",
            manifest_url=None,
            compatibility={"verified": "13.300"},
            system_compatibility={},
            module_requirements=[rel_a],
            download_url=None,
            source="test",
        )
        release_b = ReleaseRecord(
            version="1.1.0",
            manifest_url=None,
            compatibility={"verified": "13.300"},
            system_compatibility={},
            module_requirements=[rel_b],
            download_url=None,
            source="test",
        )

        def fetch_history(module: ModuleRecord, _: int):
            return ([release_a], []) if module.module_id == "a" else ([release_b], [])

        def load_relationship(rel: ModuleRelationship):
            return {"a": a, "b": b}.get(rel.module_id)

        recommendation, warnings = resolve_module_recommendation(
            a,
            "13.350",
            {},
            fetch_history,
            load_relationship,
            {},
        )

        self.assertEqual(recommendation.module, "a")
        self.assertTrue("a" in warnings or "b" in warnings)


if __name__ == "__main__":
    unittest.main()
