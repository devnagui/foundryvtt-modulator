from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3

from backend.app.services import runtime as runtime_mod


class RuntimeSuggestionCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp_dir.name) / "foundry"
        (self.data_root / "Data" / "modules").mkdir(parents=True, exist_ok=True)
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(data_root=str(self.data_root), cache_dir=str(self.data_root / ".cache")),
            config_store=SimpleNamespace(get_data_root=lambda: str(self.data_root)),
        )

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass
        runtime_mod._REPORT_SUGGEST_CACHE.clear()

    def test_invalidate_cache_removes_only_target_module_entries(self) -> None:
        runtime_mod._REPORT_SUGGEST_CACHE.clear()
        runtime_mod._REPORT_SUGGEST_CACHE["dae|a|b|c|{}"] = {"ok": 1}
        runtime_mod._REPORT_SUGGEST_CACHE["DAE|x|y|z|{}"] = {"ok": 2}
        runtime_mod._REPORT_SUGGEST_CACHE["socketlib|a|b|c|{}"] = {"ok": 3}

        removed = runtime_mod._invalidate_report_suggest_cache_for_modules(["dae"])

        self.assertEqual(2, removed)
        self.assertNotIn("dae|a|b|c|{}", runtime_mod._REPORT_SUGGEST_CACHE)
        self.assertNotIn("DAE|x|y|z|{}", runtime_mod._REPORT_SUGGEST_CACHE)
        self.assertIn("socketlib|a|b|c|{}", runtime_mod._REPORT_SUGGEST_CACHE)

    def test_suggest_module_force_refresh_invalidates_module_cache(self) -> None:
        with patch("backend.app.services.runtime._validate_foundry_root_path", return_value=(True, str(self.data_root), {})), patch(
            "backend.app.services.runtime.detect_foundry_version",
            return_value=("13.351", "diagnostics"),
        ), patch(
            "backend.app.services.runtime.load_system_versions",
            return_value={"dnd5e": "5.3.0"},
        ), patch(
            "backend.app.services.runtime._invalidate_report_suggest_cache_for_modules",
            return_value=1,
        ) as invalidate_mock, patch(
            "backend.app.services.runtime._suggest_best_release_for_module",
            return_value={"recommendedVersion": "12.0.0"},
        ):
            result = runtime_mod.suggest_module(
                self.runtime,
                module_id="dae",
                manifest_url="",
                project_url="https://github.com/tposney/dae",
                force_refresh=True,
            )

        self.assertTrue(bool(result.get("ok")))
        invalidate_mock.assert_called_once_with(["dae"])

    def test_suggest_module_force_refresh_retries_transient_provider_failures(self) -> None:
        attempts = {"count": 0}

        def _flaky(*_args, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("timeout while fetching provider")
            return {"recommendedVersion": "13.0.0"}

        with patch("backend.app.services.runtime._validate_foundry_root_path", return_value=(True, str(self.data_root), {})), patch(
            "backend.app.services.runtime.detect_foundry_version",
            return_value=("13.351", "diagnostics"),
        ), patch(
            "backend.app.services.runtime.load_system_versions",
            return_value={"dnd5e": "5.3.0"},
        ), patch(
            "backend.app.services.runtime._suggest_best_release_for_module",
            side_effect=_flaky,
        ):
            result = runtime_mod.suggest_module(
                self.runtime,
                module_id="dae",
                manifest_url="",
                project_url="https://github.com/tposney/dae",
                force_refresh=True,
            )

        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(3, attempts["count"])

    def test_force_refresh_bypasses_stale_report_suggestion_cache(self) -> None:
        module_id = "dae"
        project_url = "https://github.com/tposney/dae"
        key = runtime_mod._report_suggest_cache_key(
            module_id=module_id,
            manifest_url="",
            project_url=project_url,
            target_foundry_version="14.361",
            installed_system_versions={"dnd5e": "5.3.3"},
        )
        runtime_mod._REPORT_SUGGEST_CACHE.clear()
        runtime_mod._REPORT_SUGGEST_CACHE[key] = {"recommendedVersion": "old"}

        with patch(
            "backend.app.services.runtime._suggest_best_release_for_module_with_caches",
            return_value={"recommendedVersion": "new"},
        ) as suggest_mock:
            cached = runtime_mod._resolve_suggestion_from_sources_with_caches(
                runtime=self.runtime,
                module_id=module_id,
                sources={module_id: {"projectUrl": project_url}},
                target_foundry_version="14.361",
                installed_system_versions={"dnd5e": "5.3.3"},
                installed_modules_by_id={},
                resolution_cache={},
                history_cache={},
                force_refresh=False,
            )
            fresh = runtime_mod._resolve_suggestion_from_sources_with_caches(
                runtime=self.runtime,
                module_id=module_id,
                sources={module_id: {"projectUrl": project_url}},
                target_foundry_version="14.361",
                installed_system_versions={"dnd5e": "5.3.3"},
                installed_modules_by_id={},
                resolution_cache={},
                history_cache={},
                force_refresh=True,
            )

        self.assertEqual("old", str((cached or {}).get("recommendedVersion") or ""))
        self.assertEqual("new", str((fresh or {}).get("recommendedVersion") or ""))
        self.assertEqual(1, suggest_mock.call_count)

    def test_invalidate_planning_context_rows_removes_only_target_modules(self) -> None:
        state_dir = Path(self.temp_dir.name) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / "resolver.db"
        with sqlite3.connect(db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT,
                    target_version TEXT,
                    data_root TEXT,
                    dry_run INTEGER,
                    apply_mode INTEGER,
                    payload_json TEXT
                );
                CREATE TABLE planning_context_rows (
                    scan_run_id INTEGER NOT NULL,
                    context_key TEXT NOT NULL,
                    foundry_version TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    system_version TEXT NOT NULL,
                    module_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    has_missing_dependencies INTEGER NOT NULL,
                    title TEXT,
                    installed_version TEXT,
                    recommended_version TEXT,
                    reason TEXT,
                    compatibility_json TEXT NOT NULL,
                    system_compatibility_json TEXT NOT NULL,
                    PRIMARY KEY (scan_run_id, context_key, module_id)
                );
                """
            )
            connection.execute(
                "INSERT INTO scan_runs(generated_at,target_version,data_root,dry_run,apply_mode,payload_json) VALUES(?,?,?,?,?,?)",
                ("2026-05-14T00:00:00Z", "14.361", "D:/foundry", 1, 0, "{}"),
            )
            connection.execute(
                """
                INSERT INTO planning_context_rows(
                    scan_run_id, context_key, foundry_version, system_id, system_version, module_id,
                    status, has_missing_dependencies, title, installed_version, recommended_version, reason,
                    compatibility_json, system_compatibility_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (1, "14.361::dnd5e@5.3.3", "14.361", "dnd5e", "5.3.3", "dae", "update", 0, "DAE", "13.0.26", "13.0.30", "upgrade", "{}", "{}"),
            )
            connection.execute(
                """
                INSERT INTO planning_context_rows(
                    scan_run_id, context_key, foundry_version, system_id, system_version, module_id,
                    status, has_missing_dependencies, title, installed_version, recommended_version, reason,
                    compatibility_json, system_compatibility_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (1, "14.361::dnd5e@5.3.3", "14.361", "dnd5e", "5.3.3", "socketlib", "ready", 0, "Socketlib", "1.0.0", "1.0.0", "ok", "{}", "{}"),
            )
            connection.commit()

        runtime = SimpleNamespace(
            config=SimpleNamespace(state_dir=str(state_dir)),
        )
        removed = runtime_mod._invalidate_planning_context_rows(runtime, ["dae"])
        self.assertEqual(1, removed)
        with sqlite3.connect(db_path) as connection:
            remaining = connection.execute("SELECT module_id FROM planning_context_rows ORDER BY module_id").fetchall()
        self.assertEqual([("socketlib",)], remaining)


if __name__ == "__main__":
    unittest.main()
