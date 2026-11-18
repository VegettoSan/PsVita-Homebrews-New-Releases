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

    def test_verified_renamed_repo_includes_canonical_provenance(self):
        latest = entry("v2.1", False, 448956819)
        latest["html_url"] = "https://github.com/PupueStudios/sonic-r-vita/releases/tag/v2.1"
        latest["assets"][0]["browser_download_url"] = (
            "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/sonicr_vita.vpk"
        )
        def response(url, **kwargs):
            if url.endswith("/releases"):
                return [latest]
            if url.endswith("/repos/fr3dsp/sonic-r-vita"):
                return {"full_name": "PupueStudios/sonic-r-vita", "id": 1378201322}
            raise AssertionError("Unexpected URL: " + url)
        with patch.object(monitor, "request_json", side_effect=response):
            result = monitor.get_github_release("fr3dsp/sonic-r-vita")
        self.assertEqual(result["repo"], "fr3dsp/sonic-r-vita")
        self.assertEqual(result["canonical_repo"], "PupueStudios/sonic-r-vita")
        self.assertEqual(result["repo_id"], 1378201322)

    def test_unverified_repo_move_never_adds_canonical_provenance(self):
        latest = entry("v2.1", False, 448956819)
        latest["html_url"] = "https://github.com/unrelated/other/releases/tag/v2.1"
        with patch.object(monitor, "request_json",
                          side_effect=[[latest], {"full_name": "unrelated/different", "id": 123}]):
            result = monitor.get_github_release("fr3dsp/sonic-r-vita")
        self.assertNotIn("canonical_repo", result)
        self.assertNotIn("repo_id", result)

    def test_github_confirms_no_stable_release_after_twenty_prereleases(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        urls = []
        def response(url, **kwargs):
            urls.append(url)
            if url.endswith("/releases"):
                return releases
            if url.endswith("/releases/latest"):
                return None  # only an HTTP 404, never an API error, yields None
            raise AssertionError(url)
        with patch.object(monitor, "request_json", side_effect=response):
            record = monitor.get_github_release("dev/port")
        self.assertTrue(record["no_stable_release"])
        self.assertIn("/releases/latest", urls[-1])

    def test_older_stable_is_checked_even_after_twenty_prereleases(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        stable = entry("v0.9", False, 99)
        with patch.object(monitor, "request_json",
                          side_effect=[releases, stable]):
            record = monitor.get_github_release("dev/port")
        self.assertNotIn("no_stable_release", record)
        self.assertEqual(record["stable_release"]["tag"], "v0.9")

    def test_stable_lookup_error_does_not_claim_stable_absence(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        with patch.object(monitor, "request_json",
                          side_effect=[releases, RuntimeError("API rate limit")]):
            record = monitor.get_github_release("dev/port")
        self.assertNotIn("no_stable_release", record)
        self.assertNotIn("stable_release", record)

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
