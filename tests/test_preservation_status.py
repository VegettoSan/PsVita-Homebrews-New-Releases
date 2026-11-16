#!/usr/bin/env python3
"""Offline tests for Archive.org backup labels; no HTTP calls."""
import unittest

from preservation_status import (
    archive_vpk_url, assess_backup, filename_version,
    find_archive_vpk_urls, parse_numeric_version,
)

ROOT = "https://archive.org/download/psvitaalive-example/"


class PreservationStatusTests(unittest.TestCase):
    def test_identifies_versioned_archive_filename(self):
        self.assertEqual(filename_version(ROOT + "example-v1.3.0.vpk"), (1, 3, 0, 0))
        self.assertEqual(filename_version(ROOT + "example_01.02.vpk"), (1, 2, 0, 0))
        self.assertEqual(filename_version(ROOT + "Example%20v.2.0.vpk"), (2, 0, 0, 0))
        self.assertEqual(parse_numeric_version("v.1.2.0"), (1, 2, 0, 0))
        self.assertIsNone(filename_version(ROOT + "example-v1.3.0-beta.vpk"))

    def test_legacy_without_version_is_unknown_not_missing(self):
        self.assertIsNone(filename_version(ROOT + "example.vpk"))
        self.assertIsNone(filename_version(ROOT + "2048.vpk"))
        self.assertIsNone(filename_version(ROOT + "z2.vpk"))
        state, _ = assess_backup(
            release_tag="v1.3.0", catalog_version="1.2",
            archive_urls=[ROOT + "example.vpk"], is_registered=True,
        )
        self.assertEqual(state, "unversioned")

    def test_archive_link_selection_ignores_images_and_item_pages(self):
        self.assertTrue(archive_vpk_url(ROOT + "example-v1.0.vpk?download=1"))
        self.assertFalse(archive_vpk_url(ROOT + "icon.png"))
        self.assertFalse(archive_vpk_url("https://archive.org/details/psvitaalive-example"))
        self.assertFalse(archive_vpk_url("https://github.com/example/repo/releases/download/v1/example.vpk"))
        links = [
            {"type": "Download", "url": ROOT + "example-v1.3.vpk"},
            {"type": "Mirror", "url": ROOT + "example-v1.3.vpk"},
            {"type": "Repository", "url": "https://github.com/example/repo"},
            {"type": "Other", "url": ROOT + "icon.png"},
        ]
        self.assertEqual(find_archive_vpk_urls(links), [ROOT + "example-v1.3.vpk"])

    def test_status_matched_outdated_and_missing(self):
        common = {"release_tag": "1.3", "catalog_version": "1.2", "is_registered": True}
        result = assess_backup(**common, archive_urls=[ROOT + "example-1.3.0.vpk"])
        self.assertEqual(result[0], "matched")
        self.assertIn("sin comprobar", result[1])
        self.assertEqual(
            assess_backup(**common, archive_urls=[ROOT + "example-1.2.0.vpk"])[0],
            "outdated",
        )
        self.assertEqual(assess_backup(**common, archive_urls=[])[0], "missing")

    def test_catalog_newer_than_release_is_target(self):
        self.assertEqual(
            assess_backup(release_tag="v1.2", catalog_version="v1.3",
                          archive_urls=[ROOT + "example-v1.2.vpk"], is_registered=True)[0],
            "outdated",
        )

    def test_does_not_guess_when_unregistered_or_uncomparable(self):
        self.assertEqual(
            assess_backup(release_tag="v1.2", catalog_version="",
                          archive_urls=[], is_registered=False)[0],
            "unknown",
        )
        self.assertEqual(
            assess_backup(release_tag="nightly", catalog_version="",
                          archive_urls=[ROOT + "example.vpk"], is_registered=True)[0],
            "review",
        )


if __name__ == "__main__":
    unittest.main()
