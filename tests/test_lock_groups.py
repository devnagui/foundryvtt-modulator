"""Tests for LockGroupStore CRUD operations."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.core import ServiceConfig
from backend.app.services.lock_groups import LockGroupStore


def _make_config(tmp: str) -> ServiceConfig:
    return ServiceConfig(
        tool_root=Path(tmp),
        data_root=tmp,
        cache_dir=str(Path(tmp) / ".cache"),
        reports_dir=Path(tmp) / "reports",
        state_dir=Path(tmp) / "state",
        auth_file=Path(tmp) / "state" / "auth.json",
        bind_host="0.0.0.0",
        bind_port=8787,
        python_bin="python",
        session_ttl_hours=24,
        pbkdf2_iterations=1,
        require_foundry_offline=False,
        foundry_host="localhost",
        foundry_port=30000,
        foundry_process_name="FoundryVTT",
        cookie_secure=False,
        auth_max_failed_attempts=5,
        auth_lockout_minutes=15,
        request_rate_limit_per_minute=120,
        max_sessions=10,
        audit_file=Path(tmp) / "state" / "audit.log.jsonl",
    )


class TestLockGroupStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.config = _make_config(self.tmp)
        self.store = LockGroupStore(self.config)

    def test_list_empty(self) -> None:
        self.assertEqual(self.store.list_groups(), [])

    def test_create_and_list(self) -> None:
        group = self.store.create_group({
            "name": "Combat Suite v13",
            "foundryVersion": "13.351",
            "entries": [
                {"packageId": "midi-qol", "packageKind": "module", "version": "13.0.57", "verified": True, "required": True, "notes": "Last before 5.3"},
                {"packageId": "dnd5e", "packageKind": "system", "version": "5.2.5", "verified": True, "required": True},
            ],
        })
        self.assertTrue(group["id"])
        self.assertEqual(group["name"], "Combat Suite v13")
        self.assertEqual(group["foundryVersion"], "13.351")
        self.assertEqual(len(group["entries"]), 2)
        self.assertEqual(group["entries"][0]["packageId"], "midi-qol")
        self.assertEqual(group["entries"][0]["version"], "13.0.57")
        self.assertTrue(group["entries"][0]["verified"])
        self.assertEqual(group["entries"][1]["packageKind"], "system")
        groups = self.store.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["id"], group["id"])

    def test_get_group(self) -> None:
        group = self.store.create_group({"name": "Test", "entries": [{"packageId": "a", "version": "1.0"}]})
        found = self.store.get_group(group["id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "Test")
        self.assertIsNone(self.store.get_group("nonexistent"))

    def test_update_group(self) -> None:
        group = self.store.create_group({"name": "Original", "entries": [{"packageId": "a", "version": "1.0"}]})
        updated = self.store.update_group(group["id"], {
            "name": "Updated",
            "foundryVersion": "14.0",
            "entries": [
                {"packageId": "a", "version": "2.0", "verified": True},
                {"packageId": "b", "version": "1.0"},
            ],
        })
        self.assertEqual(updated["id"], group["id"])
        self.assertEqual(updated["name"], "Updated")
        self.assertEqual(updated["foundryVersion"], "14.0")
        self.assertEqual(len(updated["entries"]), 2)
        self.assertEqual(updated["entries"][0]["version"], "2.0")
        self.assertEqual(updated["createdAt"], group["createdAt"])
        self.assertNotEqual(updated["updatedAt"], group["updatedAt"])

    def test_update_nonexistent_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.update_group("bogus", {"name": "X", "entries": []})

    def test_delete_group(self) -> None:
        g1 = self.store.create_group({"name": "A", "entries": [{"packageId": "x", "version": "1"}]})
        g2 = self.store.create_group({"name": "B", "entries": [{"packageId": "y", "version": "2"}]})
        self.assertTrue(self.store.delete_group(g1["id"]))
        self.assertEqual(len(self.store.list_groups()), 1)
        self.assertEqual(self.store.list_groups()[0]["id"], g2["id"])
        self.assertFalse(self.store.delete_group("nonexistent"))

    def test_create_requires_name(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_group({"name": "", "entries": []})

    def test_entries_skip_empty_version(self) -> None:
        group = self.store.create_group({"name": "T", "entries": [
            {"packageId": "a", "version": "1.0"},
            {"packageId": "b", "version": ""},
            {"packageId": "c"},
        ]})
        self.assertEqual(len(group["entries"]), 1)
        self.assertEqual(group["entries"][0]["packageId"], "a")

    def test_entries_deduplicate(self) -> None:
        group = self.store.create_group({"name": "T", "entries": [
            {"packageId": "a", "version": "1.0"},
            {"packageId": "a", "version": "2.0"},
        ]})
        self.assertEqual(len(group["entries"]), 1)
        self.assertEqual(group["entries"][0]["version"], "1.0")

    def test_entries_normalize_kind(self) -> None:
        group = self.store.create_group({"name": "T", "entries": [
            {"packageId": "a", "packageKind": "invalid", "version": "1.0"},
            {"packageId": "b", "packageKind": "system", "version": "2.0"},
        ]})
        self.assertEqual(group["entries"][0]["packageKind"], "module")
        self.assertEqual(group["entries"][1]["packageKind"], "system")

    def test_persistence(self) -> None:
        self.store.create_group({"name": "Persist", "entries": [{"packageId": "x", "version": "1"}]})
        store2 = LockGroupStore(self.config)
        groups = store2.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "Persist")

    def test_file_format(self) -> None:
        self.store.create_group({"name": "Check", "entries": [{"packageId": "x", "version": "1"}]})
        path = self.config.state_dir / "version-lock-groups.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("groups", data)
        self.assertIsInstance(data["groups"], list)


if __name__ == "__main__":
    unittest.main()
