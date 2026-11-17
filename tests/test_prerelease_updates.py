#!/usr/bin/env python3
"""The monitor keeps prereleases, stable fallback, and safe version ordering."""
import unittest
from unittest.mock import patch

import monitor
from enrich_dashboard import compare_versions


def entry(tag, prerelease, release_id):
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "published_at": "2026-11-16T10:00:00Z",
        "prerelease": prerelease,
        "assets": [{
            "name": "port.vpk",
            "size": 100,
            "browser_download_url": "https://github.com/dev/port/releases/download/" + tag + "/port.vpk",
        }],
    }


class PrereleaseMonitorTests(unittest.TestCase):
    def test_prerelease_kept_and_stable_fallback_exposed(self):
        releases = [
            entry("v1.2.0-beta.2", True, 12),
            entry("v1.1.0", False, 11),
        ]
        with patch.object(monitor, "request_json", return_value=releases) as request:
            result = monitor.get_github_release("dev/port")
        self.assertEqual(request.call_args.kwargs["params"], {"per_page": 20})
        self.assertEqual(result["tag"], "v1.2.0-beta.2")
        self.assertTrue(result["prerelease"])
        self.assertEqual(result["assets"][0]["name"], "port.vpk")
        stable = result["stable_release"]
        self.assertFalse(stable["prerelease"])
        self.assertEqual(stable["tag"], "v1.1.0")
        self.assertEqual(stable["assets"][0]["name"], "port.vpk")

    def test_prerelease_only_has_no_false_stable_fallback(self):
        with patch.object(monitor, "request_json", return_value=[
            entry("v1.2.0-rc1", True, 12), entry("v1.2.0-beta2", True, 11),
        ]):
            result = monitor.get_github_release("dev/port")
        self.assertTrue(result["prerelease"])
        self.assertNotIn("stable_release", result)

    def test_drafts_ignored(self):
        draft = entry("v3.0", False, 13)
        draft["draft"] = True
        with patch.object(monitor, "request_json", return_value=[
            draft, entry("v1.2.0-beta2", True, 12),
        ]):
            result = monitor.get_github_release("dev/port")
        self.assertEqual(result["tag"], "v1.2.0-beta2")

    def test_dashboard_semantic_order_and_incomparables(self):
        self.assertEqual(compare_versions("1.2.0-beta2", "1.2.0-beta3"), -1)
        self.assertEqual(compare_versions("1.2.0-rc1", "1.2.0"), -1)
        self.assertEqual(compare_versions("1.2.0", "1.2.0-beta3"), 1)
        self.assertEqual(compare_versions("1.2.0-beta2", "1.2.0"), -1)
        self.assertEqual(compare_versions("1.2", "v1.2.0"), 0)
        self.assertIsNone(compare_versions("1.2.0", "continuous"))
        self.assertIsNone(compare_versions("1.2.0", "v1.2.0-multicore.2"))


if __name__ == "__main__":
    unittest.main()
