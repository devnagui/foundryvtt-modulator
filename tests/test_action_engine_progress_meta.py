from __future__ import annotations

import unittest

from backend.app.services.core import ActionEngine


class ActionEngineProgressMetaTests(unittest.TestCase):
    def test_set_progress_merges_meta_and_exposes_in_job_payload(self) -> None:
        engine = ActionEngine()
        job = engine.enqueue("override-from-plan", {"planContent": "{}"})
        picked = engine.pick_next()
        self.assertIsNotNone(picked)

        engine.set_progress(job.job_id, 42, {"phase": "resolving", "totalItems": 10, "processedItems": 3})
        engine.set_progress(job.job_id, 58, {"currentItemKind": "module", "currentItemId": "dae"})

        status = engine.get_job(job.job_id) or {}
        self.assertEqual(58, int(status.get("progress") or 0))
        meta = status.get("progressMeta") or {}
        self.assertEqual("resolving", str(meta.get("phase") or ""))
        self.assertEqual(10, int(meta.get("totalItems") or 0))
        self.assertEqual(3, int(meta.get("processedItems") or 0))
        self.assertEqual("module", str(meta.get("currentItemKind") or ""))
        self.assertEqual("dae", str(meta.get("currentItemId") or ""))


if __name__ == "__main__":
    unittest.main()
