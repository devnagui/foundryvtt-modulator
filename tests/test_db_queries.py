import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from resolver.db_queries import load_apply_history


class TestDbQueries(unittest.TestCase):
    def test_load_apply_history_returns_apply_runs(self) -> None:
        tmp = tempfile.mkdtemp(prefix="resolver-dbq-")
        db = Path(tmp) / "resolver.db"
        try:
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, target_version TEXT, data_root TEXT, dry_run INTEGER, apply_mode INTEGER, payload_json TEXT)"
                )
                payload_apply = {
                    "dependencyApplyActions": [
                        {"module": "midi-qol", "backupPath": "Backups/modules/midi-qol/a.bak"},
                        {"module": "lib-wrapper", "backupPath": "Backups/modules/lib-wrapper/a.bak"},
                    ]
                }
                payload_dry = {"dependencyApplyActions": []}
                conn.execute(
                    "INSERT INTO scan_runs(generated_at,target_version,data_root,dry_run,apply_mode,payload_json) VALUES(?,?,?,?,?,?)",
                    ("2026-05-11T20:00:00Z", "13.351", "D:/x", 0, 1, json.dumps(payload_apply)),
                )
                conn.execute(
                    "INSERT INTO scan_runs(generated_at,target_version,data_root,dry_run,apply_mode,payload_json) VALUES(?,?,?,?,?,?)",
                    ("2026-05-11T21:00:00Z", "13.351", "D:/x", 1, 0, json.dumps(payload_dry)),
                )
                conn.commit()

            rows = load_apply_history(str(db), limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["modulesChangedCount"], 2)
            self.assertEqual(rows[0]["backupsCreatedCount"], 2)
        finally:
            try:
                if db.exists():
                    db.unlink()
            except OSError:
                pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
