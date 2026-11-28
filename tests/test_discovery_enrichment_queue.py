import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import requests
import discover_homebrew as discovery

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

    def test_publication_lane_reserves_capacity_for_historical_candidates(self):
        historical = {"platform":"github","project_type":"homebrew","project":"dev/demo","app_draft":{
            "version_date":"2020-01-01","size":100,"description":"Demo","links":[{"type":"Download","url":"https://github.com/dev/demo/releases/download/v1/demo.vpk"}]}}
        self.candidates["c"].update(historical)
        q.reconcile(self.state,self.candidates)
        self.assertEqual([k for k,_ in q.choose(self.state,self.candidates,2,self.now,prioritize_publication=True)],["c","a"])
        q.checkpoint(self.state,"c",{"state":"inspected"},self.now)
        self.assertEqual([k for k,_ in q.choose(self.state,self.candidates,2,self.now,prioritize_publication=True)],["a","b"])

    def test_operator_priority_respects_first_attempt_and_duplicate_filter(self):
        candidates = {}
        for key in ("gh:1", "gh:2", "gh:3"):
            project = "dev/demo" + key.split(":")[1]
            candidates[key] = {"key": key, "platform": "github", "project_type": "homebrew",
                "project": project, "app_draft": {"version_date": "2020-01-01", "size": 100,
                "description": "Demo", "links": [{"type": "Download",
                "url": "https://github.com/" + project + "/releases/download/v1/demo.vpk"}]}}
        state = q.reconcile(q.empty_state(), candidates)
        def chosen():
            return [key for key, _ in q.choose(state, candidates, 2, self.now, prioritize_publication=True)]
        with patch.object(q, "operator_priority", return_value={"gh:3"}):
            self.assertEqual(chosen(), ["gh:3", "gh:1"])
            state["publication_queue"] = {"records": {"gh:3": {"state": "DUPLICATE"}}}
            self.assertEqual(chosen(), ["gh:1", "gh:2"])
            state.pop("publication_queue")
            state["records"]["gh:3"]["next_at"] = (self.now + timedelta(hours=1)).isoformat()
            self.assertEqual(chosen(), ["gh:1", "gh:2"])
            state["records"]["gh:3"]["next_at"] = ""
            state["records"]["gh:3"]["attempts"] = 1
            self.assertEqual(chosen(), ["gh:1", "gh:2"])

    def test_operator_priority_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "priority.json"
            path.write_text('{"schema_version":1,"keys":["gh:3"],"expires_at":"2026-11-28T00:00:00Z"}')
            self.assertEqual(q.operator_priority(self.now, path), {"gh:3"})
            self.assertEqual(q.operator_priority(self.now + timedelta(days=1), path), set())

    def test_recent_promising_and_ready_snapshot(self):
        entry = {"key": "gh:123", "platform": "github", "project_type": "homebrew",
                 "project": "dev/demo", "app_draft": {"id": "demo", "description": "Demo",
                 "version_date": self.now.date().isoformat(), "size": 100,
                 "links": [{"type": "Download", "url": "https://github.com/dev/demo/releases/download/v1/demo.vpk"}]}}
        candidates = {"gh:123": entry}
        state = q.reconcile(q.empty_state(), candidates)
        self.assertTrue(q.publication_priority(entry, self.now))
        result = {"state": "inspected", "title": {"state": "high", "suggestion": "DEMO00001"},
                  "vpk": {"state": "verified", "title_id": "DEMO00001", "crc_checked": True,
                          "bytes_read": 100, "sha256": "a"*64, "artwork": {"icon": True},
                          "url": entry["app_draft"]["links"][0]["url"]},
                  "category": {"state": "suggested", "category_id": "demos", "subcategory_ids": ["other"]},
                  "author": {"state": "draft"}, "requirements": {"state": "no_data"}}
        result["vpk"].update(tag="v1", asset_id=123)
        result["publication_preflight"] = {"state": "verified", "checked_at": self.now.isoformat(),
            "asset": {"state": "selected", "size": 100, "url": result["vpk"]["url"], "tag": "v1", "asset_id": 123}}
        taxonomy = [{"id": "demos", "subcategories": [{"id": "other"}]}]
        self.assertTrue(q.source_proof_matches(result, self.now))
        self.assertFalse(q.source_proof_matches(result, self.now + timedelta(hours=25)))
        self.assertFalse(q.source_proof_matches({**result, "publication_preflight": None}, self.now))
        q.checkpoint(state, "gh:123", result, self.now)
        snap = q.publication_snapshot(state, candidates, [], {}, taxonomy, now=self.now)
        self.assertEqual(snap["counts"], {"READY_TO_PUBLISH": 1})
        feedback = {"gh:123": {"evidence": q.evidence_key(entry, result), "reason": "upstream_missing",
                              "retry_at": (self.now + timedelta(days=7)).isoformat()}}
        self.assertEqual(q.publication_snapshot(state, candidates, [], {}, taxonomy, feedback, self.now)["counts"], {"RETRY_LATER": 1})
        self.assertEqual(q.publication_snapshot(state, candidates, [{"id": "demo"}], {}, taxonomy, now=self.now)["counts"], {"DUPLICATE": 1})
        entry["app_draft"]["version_date"] = "2026-11-26"
        q.reconcile(state, candidates)
        self.assertEqual(q.publication_snapshot(state, candidates, [], {}, taxonomy, feedback, self.now)["counts"], {"PROMISING": 1})

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



class QuotaTests(unittest.TestCase):
    def setUp(self):
        for key, value in (("REQUEST_COUNT", 0), ("LOW_RATE_LIMIT", False),
                           ("RATE_RESERVE", 30), ("RATE_REMAINING", None),
                           ("RATE_RESET", None), ("RATE_STOP_REASON", "")):
            p = patch.object(discovery, key, value)
            p.start()
            self.addCleanup(p.stop)

    def response(self, status, remaining, text="{}", extra=None):
        r = requests.Response()
        r.status_code = status
        r.url = "https://api.github.com/repos/dev/demo"
        r._content = text.encode()
        r.headers.update({"X-RateLimit-Remaining": str(remaining),
                          "X-RateLimit-Reset": "1795806000", **(extra or {})})
        return r

    def test_forbidden_repository_is_not_global_rate_limit(self):
        with patch.object(discovery.HTTP, "get", return_value=self.response(403, 500, '{"message":"Forbidden"}')):
            with self.assertRaises(requests.HTTPError):
                discovery.request("https://api.github.com/repos/dev/demo")
        self.assertFalse(discovery.LOW_RATE_LIMIT)
        self.assertEqual(discovery.REQUEST_COUNT, 1)

    def test_real_rate_limit_stops_and_reports_reset(self):
        with patch.object(discovery.HTTP, "get", return_value=self.response(403, 0, '{"message":"API rate limit exceeded"}')):
            with self.assertRaises(discovery.DiscoveryBudgetReached):
                discovery.request("https://api.github.com/repos/dev/demo")
        self.assertTrue(discovery.quota_status()["rate_limited"])
        self.assertEqual(discovery.quota_status()["remaining"], 0)
        self.assertTrue(discovery.quota_status()["reset_at"])

    def test_reserve_blocks_next_call_without_network(self):
        with patch.object(discovery.HTTP, "get", return_value=self.response(200, 30)) as get:
            discovery.request("https://api.github.com/repos/dev/demo")
            with self.assertRaises(discovery.DiscoveryBudgetReached):
                discovery.request("https://api.github.com/repos/dev/other")
            self.assertEqual(get.call_count, 1)

    def test_secondary_throttle_with_remaining_quota(self):
        with patch.object(discovery.HTTP, "get", return_value=self.response(429, 500, extra={"Retry-After": "60"})):
            with self.assertRaises(discovery.DiscoveryBudgetReached):
                discovery.request("https://api.github.com/repos/dev/demo")
        self.assertTrue(discovery.LOW_RATE_LIMIT)

    def test_network_failure_counts_attempt(self):
        with patch.object(discovery.HTTP, "get", side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                discovery.request("https://api.github.com/repos/dev/demo")
        self.assertEqual(discovery.REQUEST_COUNT, 1)

    def test_public_raw_request_never_sends_provider_token(self):
        with patch.dict(discovery.HEADERS, {"Authorization": "Bearer synthetic-fixture"}):
            with patch.object(discovery.HTTP, "get", return_value=self.response(200, 500)) as get:
                discovery.request("https://raw.githubusercontent.com/dev/demo/main/file.json")
                self.assertNotIn("Authorization", get.call_args.kwargs["headers"])



class OptimizationTests(unittest.TestCase):
    now = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)

    def test_quota_pause_survives_checkpoint_and_expires(self):
        state = q.empty_state()
        reset = self.now + timedelta(minutes=12)
        q.apply_quota_pause(state, {"rate_limited": True, "reset_at": reset.isoformat()}, self.now)
        self.assertTrue(q.paused(state, self.now))
        self.assertTrue(q.paused(state, reset))
        self.assertFalse(q.paused(state, reset + timedelta(seconds=61)))
        q.apply_quota_pause(state, {"rate_limited": False}, reset)
        self.assertNotIn("resume_at", state)

    def test_public_profile_cache_matches_numeric_identity_and_expires(self):
        state = q.empty_state()
        calls = []
        url = "https://api.github.com/users/demo"
        def request(url):
            calls.append(url)
            return {"id": 12, "type": "User", "login": "demo", "html_url": "https://github.com/demo",
                    "name": "Demo", "private_field": "must not persist"}
        lookup = q.profile_request(state, {"id": 12}, request, self.now)
        lookup(url)
        cached = q.profile_request(state, {"id": 12}, request, self.now)(url)
        self.assertEqual(cached["id"], 12)
        self.assertNotIn("private_field", cached)
        self.assertEqual(len(calls), 1)
        q.profile_request(state, {"id": 99}, request, self.now)(url)
        self.assertEqual(len(calls), 2)
        q.profile_request(state, {"id": 12}, request, self.now + timedelta(days=8))(url)
        self.assertEqual(len(calls), 3)

    def test_raw_is_bounded_and_does_not_use_core_quota_or_token(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {}
        response.iter_content.return_value = iter([b"hello", b" world"])
        with patch.object(discovery.HTTP, "get", return_value=response) as get, patch.object(discovery, "REQUEST_COUNT", 0), patch.object(discovery, "RAW_REQUEST_COUNT", 0), patch.object(discovery, "CORE_REQUEST_COUNT", 0), patch.object(discovery, "LOW_RATE_LIMIT", True):
            self.assertEqual(discovery.raw_bytes("dev/demo", "a"*40, "docs/README.md"), b"hello world")
            self.assertEqual(discovery.RAW_REQUEST_COUNT, 1)
            self.assertEqual(discovery.CORE_REQUEST_COUNT, 0)
            self.assertNotIn("Authorization", get.call_args.kwargs["headers"])
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            response.iter_content.return_value = iter([b"123456"])
            self.assertIsNone(discovery.raw_bytes("dev/demo", "a"*40, "README.md", 5))
            with self.assertRaises(ValueError):
                discovery.raw_bytes("dev/demo", "a"*40, "../secret")
            self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
