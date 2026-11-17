#!/usr/bin/env python3
"""Offline tests for per-folder VPK and Title ID discovery."""
import base64
import unittest
from folder_release_sources import (
    read_port_identity, pick_folder_vpk, scan_supported_folders,
)

HEAD = "a" * 40
FILE_COMMIT = "b" * 40
BLOB = "c" * 40
REPO = "papilenote/vita-ports"
API = "https://api.github.com/repos/" + REPO


def blob(path, size=1800000):
    return {"path": path, "type": "blob", "sha": BLOB, "size": size}


def readme(title, version):
    txt = f"# Port\n| | |\n|---|---|\n| Title ID | {title} |\n| Version | {version} |\n"
    return {"encoding": "base64", "content": base64.b64encode(txt.encode()).decode()}


class GithubFolderDetectionTests(unittest.TestCase):
    def test_readme_version_does_not_use_game_data_version(self):
        self.assertEqual(read_port_identity(
            "| Title ID | MVSC00001 |\n| Version | 1.2 |\n"
            "iOS game version is 1.00.00, do not use it.\n"
        ), ("MVSC00001", "1.2"))
        self.assertIsNone(read_port_identity("| Title ID | MVSC00001 |\n| Version | nightly |\n"))

    def test_multivpk_selects_only_exact_version(self):
        entries = [blob("Marvel VS Capcom 2/mvc2_v1.0.vpk"),
                   blob("Marvel VS Capcom 2/mvc2_v1.1.vpk"),
                   blob("Marvel VS Capcom 2/mvc2_v1.2.vpk")]
        self.assertEqual(
            pick_folder_vpk(entries, "Marvel VS Capcom 2", "1.2")["path"],
            "Marvel VS Capcom 2/mvc2_v1.2.vpk")
        self.assertIsNone(pick_folder_vpk(entries, "Marvel VS Capcom 2", "1.3"))
        self.assertIsNone(pick_folder_vpk([
            blob("Marvel VS Capcom 2/a.vpk"), blob("Marvel VS Capcom 2/b.vpk")
        ], "Marvel VS Capcom 2", "1.2"))

    def test_scan_returns_trusted_per_app_records(self):
        tree = [
            {"path": "Marvel VS Capcom 2", "type": "tree"},
            blob("Marvel VS Capcom 2/README.md", 4293),
            blob("Marvel VS Capcom 2/mvc2_v1.0.vpk"),
            blob("Marvel VS Capcom 2/mvc2_v1.2.vpk", 1910619),
            {"path": "Tap Sonic", "type": "tree"},
            blob("Tap Sonic/README.md", 5608),
            blob("Tap Sonic/tapsonic.vpk", 1113939),
            {"path": "Bad Port", "type": "tree"},
            blob("Bad Port/README.md", 500),
            blob("Bad Port/a.vpk"), blob("Bad Port/b.vpk"),
        ]
        calls = []
        def fetch(url, *, headers=None, params=None):
            calls.append((url, params))
            if url == API + "/commits/main":
                return {"sha": HEAD}
            if url == API + f"/git/trees/{HEAD}?recursive=1":
                return {"tree": tree, "truncated": False}
            if "/contents/" in url:
                if "Marvel%20VS%20Capcom%202" in url:
                    return readme("MVSC00001", "1.2")
                if "Tap%20Sonic" in url:
                    return readme("TPSN00001", "1.0")
                return readme("BADP00001", "1.0")
            if url == API + "/commits":
                return [{"sha": FILE_COMMIT,
                         "commit": {"committer": {"date": "2026-11-16T15:10:00Z"}}}]
            raise AssertionError("Unexpected call " + url)

        found, errors = scan_supported_folders(
            [{"platform": "github", "project": REPO}], fetch,
            lambda: {"Authorization": "Bearer fake"})
        self.assertEqual(len(found), 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("not unique", errors[0])
        marvel = next(row for row in found if row["title_id"] == "MVSC00001")
        self.assertEqual(marvel["tag"], "1.2")
        self.assertEqual(marvel["blob_sha"], BLOB)
        self.assertEqual(marvel["assets"][0]["size"], 1910619)
        self.assertEqual(marvel["assets"][0]["download_url"],
                         f"https://raw.githubusercontent.com/{REPO}/{FILE_COMMIT}/Marvel%20VS%20Capcom%202/mvc2_v1.2.vpk")
        self.assertEqual(sum(1 for url, _params in calls if url == API + "/commits"), 2)

    def test_other_repos_do_not_call_github(self):
        def fail(*args, **kwargs):
            raise AssertionError("network request was unexpected")
        self.assertEqual(scan_supported_folders(
            [{"platform": "github", "project": "unrelated/repo"}], fail, dict), ([], []))

    def test_truncated_tree_fails_closed(self):
        def fetch(url, **kwargs):
            return {"sha": HEAD} if url.endswith("/commits/main") else {"tree": [], "truncated": True}
        found, errors = scan_supported_folders(
            [{"platform": "github", "project": REPO}], fetch, dict)
        self.assertEqual(found, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("truncated", errors[0])


if __name__ == "__main__":
    unittest.main()
