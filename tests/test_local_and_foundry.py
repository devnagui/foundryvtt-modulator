import json
import tempfile
import unittest
from pathlib import Path

from resolver.foundry import detect_foundry_version
from resolver.cli import _normalize_data_root
from resolver.local import (
    _extract_enabled_modules_from_text,
    build_local_dependency_map,
    load_modules,
    load_system_versions,
)


class TestLocalAndFoundry(unittest.TestCase):
    def test_normalize_data_root_accepts_data_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "FoundryRoot"
            (root / "Data" / "modules").mkdir(parents=True)
            (root / "Logs").mkdir(parents=True)
            normalized = _normalize_data_root(str(root / "Data"))
            self.assertEqual(Path(normalized), root.resolve())

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

    def test_load_modules_ignores_backup_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules_root = Path(tmp) / "Data" / "modules"
            modules_root.mkdir(parents=True)

            valid = modules_root / "valid-module"
            backup = modules_root / "_backup_20260510_midisrc_201337"
            bak_style = modules_root / "some.bak.module"
            valid.mkdir()
            backup.mkdir()
            bak_style.mkdir()

            (valid / "module.json").write_text(
                json.dumps({"id": "valid-module", "title": "Valid", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (backup / "module.json").write_text(
                json.dumps({"id": "{{name}}", "title": "{{title}}", "version": "{{version}}"}),
                encoding="utf-8",
            )
            (bak_style / "module.json").write_text(
                json.dumps({"id": "old", "title": "Old", "version": "0.1.0"}),
                encoding="utf-8",
            )

            modules = load_modules(str(modules_root))
            module_ids = sorted([m.module_id for m in modules])
            self.assertEqual(module_ids, ["valid-module"])

    def test_extract_enabled_modules_from_text_tolerates_spaces(self) -> None:
        blob = (
            '{"key": "core.moduleConfiguration", '
            '"value": "{\\"midi-qol\\":true,\\"monks-tokenbar\\":true,\\"dae\\":false}"}'
        )
        enabled = _extract_enabled_modules_from_text(blob)
        self.assertEqual(enabled, {"midi-qol", "monks-tokenbar"})


if __name__ == "__main__":
    unittest.main()
