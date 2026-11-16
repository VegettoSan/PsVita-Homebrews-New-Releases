#!/usr/bin/env python3
"""GitLab release VPK metadata is surfaced without downloading any binaries."""
import unittest
from unittest.mock import patch
from urllib.parse import quote

import monitor
from enrich_dashboard import enrich_release


PROJECT = "vita/homebrew/awesome"
DIRECT = "https://gitlab.com/vita/homebrew/awesome/-/releases/v1.1/downloads/builds/awesome.vpk"
ORIGINAL = "https://gitlab.com/vita/homebrew/awesome/-/uploads/abcd/awesome.vpk"


def gitlab_release(links):
    return [{
        "tag_name": "v1.1",
        "name": "Version 1.1",
        "released_at": "2026-11-16T12:00:00Z",
        "assets": {"links": links, "sources": [
            {"format": "zip", "url": "https://gitlab.com/vita/homebrew/awesome/-/archive/v1.1/src.zip"}
        ]},
        "_links": {"self": "https://gitlab.com/vita/homebrew/awesome/-/releases/v1.1"},
    }]


class GitLabVpkSnapshotTests(unittest.TestCase):
    def test_prefers_provider_direct_link_preserves_filename_for_vpk_count(self):
        sample = gitlab_release([{
            "name": "awesome.vpk", "url": ORIGINAL, "direct_asset_url": DIRECT,
        }])
        with patch.object(monitor, "request_json", return_value=sample) as fetch:
            record = monitor.get_gitlab_release(PROJECT)
        self.assertEqual(record["platform"], "gitlab")
        self.assertEqual(record["tag"], "v1.1")
        self.assertEqual(record["published"], "2026-11-16T12:00:00Z")
        self.assertEqual(record["assets"][0]["name"], "awesome.vpk")
        self.assertEqual(record["assets"][0]["download_url"], DIRECT)
        self.assertEqual(record["assets"][0]["original_url"], ORIGINAL)
        self.assertEqual(record["assets"][1]["name"], "source.zip")
        self.assertIn(quote(PROJECT, safe=""), fetch.call_args.args[0])

    def test_gitlab_asset_name_may_be_generic_when_url_ends_vpk(self):
        sample = gitlab_release([{"name": "Installer", "url": ORIGINAL, "direct_asset_url": DIRECT}])
        with patch.object(monitor, "request_json", return_value=sample):
            record = monitor.get_gitlab_release(PROJECT)
        self.assertEqual(record["assets"][0]["name"], "Installer")
        self.assertTrue(record["assets"][0]["original_url"].endswith(".vpk"))

    def test_manual_review_when_gitlab_release_has_no_or_multiple_vpks(self):
        app = {
            "id": "awesome",
            "name": "Awesome",
            "version": "1.0",
            "links": [{"type": "Download", "name": "VPK",
                       "url": "https://archive.org/download/awesome/v1.0.vpk"}],
        }
        for candidates, expected in [
            ([{"name": "source.zip", "download_url": "https://gitlab.com/source.zip"}],
             "release sin VPK"),
            ([{"name": "a.vpk", "download_url": DIRECT},
              {"name": "b.vpk", "download_url": DIRECT}], "2 VPK diferentes"),
        ]:
            with self.subTest(expected=expected):
                record = {
                    "platform": "gitlab", "repo": PROJECT, "tag": "v1.1",
                    "assets": candidates,
                }
                enrich_release(record, [app])
                self.assertEqual(record["catalog_update_status"], "pending")
                self.assertIn(expected, record["catalog_update_detail"])


if __name__ == "__main__":
    unittest.main()
