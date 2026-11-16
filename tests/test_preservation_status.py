#!/usr/bin/env python3
"""Current VPK Archive.org badge stays on the existing release card."""
import unittest

from preservation_status import (
    archive_vpk_url, assess_backup, current_download, filename_version,
    find_archive_vpk_urls, parse_numeric_version,
)

ARCHIVE = "https://archive.org/download/psvitaalive-example/"
GITHUB = "https://github.com/example/homebrew/releases/download/v1.3/example.vpk"


def links(url, *, mirrors=()):
    return ([{"type": "Download", "name": "VPK", "url": url, "recommended": True}]
            + [{"type": "Mirror", "name": "Versión anterior", "url": mirror}
               for mirror in mirrors])


def assess(url, catalog="v1.3", upstream="v1.3", *, mirrors=()):
    return assess_backup(
        release_tag=upstream, catalog_version=catalog,
        links=links(url, mirrors=mirrors), is_registered=True,
    )


class PreservationTests(unittest.TestCase):
    def test_archive_url_and_name(self):
        self.assertTrue(archive_vpk_url(ARCHIVE + "example-v1.3.vpk"))
        self.assertFalse(archive_vpk_url(ARCHIVE + "icon.png"))
        self.assertFalse(archive_vpk_url("https://archive.org/details/example"))
        self.assertFalse(archive_vpk_url(GITHUB))
        self.assertEqual(filename_version(ARCHIVE + "example_01.02.vpk"), (1, 2, 0, 0))
        self.assertEqual(parse_numeric_version("v.1.2.0"), (1, 2, 0, 0))

    def test_current_archive_is_marked_even_if_legacy_filename_without_version(self):
        self.assertEqual(assess(ARCHIVE + "example.vpk")[0], "matched")
        self.assertEqual(assess(ARCHIVE + "z2.vpk")[0], "matched")
        self.assertIn("sin versión", assess(ARCHIVE + "example.vpk")[1])
        self.assertEqual(assess(ARCHIVE + "example-v1.3.vpk")[0], "matched")

    def test_new_release_while_catalog_archive_is_old(self):
        self.assertEqual(assess(ARCHIVE + "example.vpk", catalog="1.2",
                                upstream="1.3")[0], "outdated")
        self.assertEqual(assess(ARCHIVE + "example-v1.2.vpk", catalog="1.2",
                                upstream="1.3")[0], "outdated")

    def test_after_automatic_switch_to_github_old_archive_is_not_current_backup(self):
        self.assertEqual(assess(GITHUB, mirrors=[ARCHIVE + "example.vpk"])[0], "missing")
        self.assertIn("respaldo anterior", assess(GITHUB, mirrors=[ARCHIVE + "example.vpk"])[1])

    def test_backup_appears_after_user_updates_recommended_url_to_archive(self):
        self.assertEqual(assess(ARCHIVE + "example-v1.3.vpk")[0], "matched")

    def test_ambiguities(self):
        self.assertEqual(assess(ARCHIVE + "example-v1.2.vpk")[0], "review")
        self.assertEqual(assess(ARCHIVE + "example.vpk", upstream="nightly")[0], "review")
        self.assertEqual(assess_backup(
            release_tag="v1.3", catalog_version="1.2", links=[], is_registered=True
        )[0], "review")
        self.assertEqual(assess_backup(
            release_tag="v1.3", catalog_version="", links=[], is_registered=False
        )[0], "unknown")

    def test_current_selection_does_not_use_an_unrelated_mirror(self):
        current = links(GITHUB, mirrors=[ARCHIVE + "example-1.3.vpk"])
        self.assertEqual(current_download(current)["url"], GITHUB)
        self.assertEqual(find_archive_vpk_urls(current), [ARCHIVE + "example-1.3.vpk"])
        current[0]["recommended"] = False
        current.append({"type": "Download", "url": ARCHIVE + "other.vpk"})
        self.assertIsNone(current_download(current))
        self.assertEqual(assess_backup(
            release_tag="1.3", catalog_version="1.3", links=current,
            is_registered=True,
        )[0], "review")


if __name__ == "__main__":
    unittest.main()
