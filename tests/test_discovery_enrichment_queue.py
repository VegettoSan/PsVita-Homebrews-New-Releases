import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discovery_enrichment_queue as q


class QueueTests(unittest.TestCase):
    now = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.candidates = {key: {"key": key, "project": "dev/" + key, "app_draft": {"name": key}}
                           for key in ("a", "b", "c")}
        self.state = q.reconcile(q.empty_state(), self.candidates)

    def test_resume_after_persisted_checkpoint(self):
        self.assertEqual([k for k, _ in q.choose(self.state, self.candidates, 1, self.now)], ["a"])
        q.checkpoint(self.state, "a", {"state": "inspected", "readiness": {"required_present": 14}}, self.now)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            q.save_state(path, self.state)
            loaded = q.load_state(path)
        self.assertEqual([k for k, _ in q.choose(loaded, self.candidates, 2, self.now)], ["b", "c"])
        self.assertEqual(loaded["records"]["a"]["result"]["readiness"]["required_present"], 14)

    def test_retry_backoff_and_fairness(self):
        q.checkpoint(self.state, "a", {"state": "candidate_error"}, self.now)
        self.assertEqual(self.state["records"]["a"]["state"], "retry")
        self.assertEqual([k for k, _ in q.choose(self.state, self.candidates, 3, self.now)], ["b", "c"])
        later = self.now + timedelta(hours=3)
        self.assertEqual([k for k, _ in q.choose(self.state, self.candidates, 3, later)], ["b", "c", "a"])

    def test_budget_deferred_does_not_count_attempt(self):
        q.checkpoint(self.state, "a", {"state": "deferred"}, self.now)
        self.assertEqual(self.state["records"]["a"]["attempts"], 0)
        self.assertEqual([k for k, _ in q.choose(self.state, self.candidates, 3, self.now)], ["b", "c"])

    def test_changed_candidate_resets_and_removed_candidate_pruned(self):
        q.checkpoint(self.state, "a", {"state": "inspected"}, self.now)
        self.candidates["a"]["app_draft"]["name"] = "new"
        del self.candidates["b"]
        q.reconcile(self.state, self.candidates)
        self.assertEqual(self.state["records"]["a"]["state"], "new")
        self.assertNotIn("b", self.state["records"])

    def test_corrupt_checkpoint_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"schema_version":99,"records":{}}')
            with self.assertRaises(ValueError):
                q.load_state(path)

    def test_metadata_complete_is_not_publication_permission(self):
        result = {"state": "inspected", "readiness": {"metadata_complete": True},
                  "vpk": {"state": "verified"}, "publication_allowed": False}
        q.checkpoint(self.state, "a", result, self.now)
        self.assertEqual(self.state["records"]["a"]["state"], "metadata_complete")
        self.assertFalse(self.state["records"]["a"]["result"]["publication_allowed"])

    def test_cache_requires_current_digest_and_identity(self):
        prior = {"state": "verified", "crc_checked": True, "title_id": "DEMO00001",
                 "asset_id": 123, "tag": "v1", "url": "https://github.com/dev/demo/releases/download/v1/demo.vpk",
                 "bytes_read": 42, "sha256": "a"*64}
        asset = {"state": "selected", "asset_id": 123, "tag": "v1", "url": prior["url"],
                 "size": 42, "digest": "sha256:" + "a"*64}
        record = {"result": {"vpk": prior}}
        self.assertTrue(q.cached_vpk(record, asset, "DEMO00001")["cache_hit"])
        for change in ({"digest": None}, {"digest": "sha256:" + "b"*64}, {"asset_id": 124}, {"size": 43}, {"tag": "v2"}):
            self.assertIsNone(q.cached_vpk(record, {**asset, **change}))
        self.assertIsNone(q.cached_vpk(record, asset, "OTHER0001"))


if __name__ == "__main__":
    unittest.main()
