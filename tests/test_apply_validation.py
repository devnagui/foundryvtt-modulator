from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from resolver import apply as apply_mod
from resolver.models import ModuleRecord, Recommendation


class ApplyValidationTests(unittest.TestCase):
    def _module(self) -> ModuleRecord:
        return ModuleRecord(
            module_id="sample-module",
            title="Sample Module",
            version="1.0.0",
            manifest_url="https://example.invalid/module.json",
            project_url="https://example.invalid",
            path="",
            raw_manifest={},
        )

    def _recommendation(self) -> Recommendation:
        return Recommendation(
            module="sample-module",
            installed_version="1.0.0",
            recommended_version="1.1.0",
            reason="test",
            confidence="high",
            verified_version="13.351",
            manifest_url="https://example.invalid/module.json",
            download_url="https://example.invalid/module.zip",
            source="test",
            checked_releases=1,
        )

    def _write_zip(self, archive_path: Path, files: dict[str, str]) -> None:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in files.items():
                archive.writestr(path, content)

    def test_apply_rejects_missing_declared_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            modules_dir = root / "Data" / "modules"
            target_dir = modules_dir / "sample-module"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.json").write_text('{"id":"sample-module","version":"1.0.0"}', encoding="utf-8")

            archive_path = root / "bad.zip"
            self._write_zip(
                archive_path,
                {
                    "module.json": '{"id":"sample-module","version":"1.1.0","compatibility":{"minimum":"13","verified":"13.351","maximum":"13.999"},"styles":["dist/app.css"]}',
                },
            )

            original_download = apply_mod.download_to_temp
            original_delete = apply_mod.delete_cached_zip
            apply_mod.download_to_temp = lambda _url: str(archive_path)
            apply_mod.delete_cached_zip = lambda _url, _cache: True
            try:
                with self.assertRaisesRegex(ValueError, "declared files missing"):
                    apply_mod.apply_recommendation(
                        module=self._module(),
                        recommendation=self._recommendation(),
                        modules_dir=str(modules_dir),
                        cache_dir=str(root / ".cache"),
                    )
            finally:
                apply_mod.download_to_temp = original_download
                apply_mod.delete_cached_zip = original_delete

    def test_apply_rejects_legacy_core_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            modules_dir = root / "Data" / "modules"
            target_dir = modules_dir / "sample-module"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.json").write_text('{"id":"sample-module","version":"1.0.0"}', encoding="utf-8")

            archive_path = root / "legacy.zip"
            self._write_zip(
                archive_path,
                {
                    "module.json": '{"id":"sample-module","version":"1.1.0","compatibility":{"minimum":"13","verified":"13.351","maximum":"13.999"},"minimumCoreVersion":"13"}',
                },
            )

            original_download = apply_mod.download_to_temp
            original_delete = apply_mod.delete_cached_zip
            apply_mod.download_to_temp = lambda _url: str(archive_path)
            apply_mod.delete_cached_zip = lambda _url, _cache: True
            try:
                with self.assertRaisesRegex(ValueError, "legacy core compatibility fields detected"):
                    apply_mod.apply_recommendation(
                        module=self._module(),
                        recommendation=self._recommendation(),
                        modules_dir=str(modules_dir),
                        cache_dir=str(root / ".cache"),
                    )
            finally:
                apply_mod.download_to_temp = original_download
                apply_mod.delete_cached_zip = original_delete

    def test_apply_rejects_unsafe_archive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            modules_dir = root / "Data" / "modules"
            target_dir = modules_dir / "sample-module"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.json").write_text('{"id":"sample-module","version":"1.0.0"}', encoding="utf-8")

            archive_path = root / "unsafe.zip"
            self._write_zip(
                archive_path,
                {
                    "../escape.txt": "bad",
                    "module.json": '{"id":"sample-module","version":"1.1.0","compatibility":{"minimum":"13","verified":"13.351","maximum":"13.999"}}',
                },
            )

            original_download = apply_mod.download_to_temp
            original_delete = apply_mod.delete_cached_zip
            apply_mod.download_to_temp = lambda _url: str(archive_path)
            apply_mod.delete_cached_zip = lambda _url, _cache: True
            try:
                with self.assertRaisesRegex(ValueError, "Unsafe archive entry traversal detected"):
                    apply_mod.apply_recommendation(
                        module=self._module(),
                        recommendation=self._recommendation(),
                        modules_dir=str(modules_dir),
                        cache_dir=str(root / ".cache"),
                    )
            finally:
                apply_mod.download_to_temp = original_download
                apply_mod.delete_cached_zip = original_delete


if __name__ == "__main__":
    unittest.main()
