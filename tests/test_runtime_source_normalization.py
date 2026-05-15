from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from backend.app.services import runtime as runtime_mod


class RuntimeSourceNormalizationTests(unittest.TestCase):
    def test_normalize_source_urls_resolves_foundry_package_page_to_manifest_and_project(self) -> None:
        html = b"""
        <html>
          <body>
            <a href="https://github.com/League-of-Foundry-Developers/scene-packer/releases/latest/download/module.json">Manifest URL</a>
            <a href="https://github.com/League-of-Foundry-Developers/scene-packer">Repository</a>
          </body>
        </html>
        """

        class _FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(runtime_mod, "urlopen", return_value=_FakeResponse(html)):
            manifest, project = runtime_mod._normalize_source_urls(
                manifest_url="",
                project_url="https://foundryvtt.com/packages/scene-packer",
            )

        self.assertEqual(
            "https://github.com/League-of-Foundry-Developers/scene-packer/releases/latest/download/module.json",
            manifest,
        )
        self.assertEqual("https://github.com/League-of-Foundry-Developers/scene-packer", project)

    def test_normalize_source_urls_keeps_foundry_url_when_unresolvable(self) -> None:
        with patch.object(runtime_mod, "urlopen", side_effect=RuntimeError("network down")):
            manifest, project = runtime_mod._normalize_source_urls(
                manifest_url="",
                project_url="https://foundryvtt.com/packages/scene-packer",
            )

        self.assertEqual("", manifest)
        self.assertEqual("https://foundryvtt.com/packages/scene-packer", project)


if __name__ == "__main__":
    unittest.main()

