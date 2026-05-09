import json
import tempfile
import unittest
from pathlib import Path

from resolver.foundry import detect_foundry_version
from resolver.local import build_local_dependency_map, load_modules, load_system_versions


class TestLocalAndFoundry(unittest.TestCase):
    def test_detect_foundry_version_from_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "Logs"
            logs.mkdir(parents=True)
            (logs / "diagnostics.json").write_text(
                json.dumps({"foundry": {"generation": "13", "build": "351"}}),
                encoding="utf-8",
            )

            version, source = detect_foundry_version(str(root))

            self.assertEqual(version, "13.351")
            self.assertTrue(source.endswith("diagnostics.json"))

    def test_detect_foundry_version_from_container_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "container_cache"
            cache.mkdir(parents=True)
            (cache / "foundryvtt-12.331.zip").write_text("", encoding="utf-8")

            version, source = detect_foundry_version(str(root))

            self.assertEqual(version, "12.331")
            self.assertTrue(source.endswith("foundryvtt-12.331.zip"))

    def test_local_module_and_dependency_map_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules_root = Path(tmp) / "Data" / "modules"
            systems_root = Path(tmp) / "Data" / "systems"
            modules_root.mkdir(parents=True)
            systems_root.mkdir(parents=True)

            mod_a = modules_root / "mod-a"
            mod_b = modules_root / "mod-b"
            mod_c = modules_root / "mod-c"
            mod_a.mkdir()
            mod_b.mkdir()
            mod_c.mkdir()

            (mod_a / "module.json").write_text(
                json.dumps(
                    {
                        "id": "mod-a",
                        "title": "Module A",
                        "version": "1.0.0",
                        "relationships": {
                            "requires": [
                                {"id": "mod-b", "type": "module"},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (mod_b / "module.json").write_text(
                json.dumps(
                    {
                        "id": "mod-b",
                        "title": "Module B",
                        "version": "1.2.0",
                        "relationships": {
                            "requires": [
                                {"id": "mod-c", "type": "module"},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (mod_c / "module.json").write_text(
                json.dumps({"id": "mod-c", "title": "Module C", "version": "0.9.0"}),
                encoding="utf-8",
            )

            sys_dir = systems_root / "dnd5e"
            sys_dir.mkdir()
            (sys_dir / "system.json").write_text(
                json.dumps({"id": "dnd5e", "version": "3.2.1"}),
                encoding="utf-8",
            )

            modules = load_modules(str(modules_root))
            dep_map = build_local_dependency_map(modules)
            systems = load_system_versions(str(Path(tmp)))

            self.assertEqual(len(modules), 3)
            self.assertEqual(dep_map["mod-a"]["direct"], ["mod-b"])
            self.assertEqual(dep_map["mod-a"]["transitive"], ["mod-b", "mod-c"])
            self.assertEqual(systems["dnd5e"], "3.2.1")


if __name__ == "__main__":
    unittest.main()
