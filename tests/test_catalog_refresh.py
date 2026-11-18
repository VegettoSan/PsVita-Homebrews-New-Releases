#!/usr/bin/env python3
"""Offline regression tests for fast catalog-publish refreshes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor


def github(project):
    return {
        "platform": "github", "project": project,
        "key": "github:" + project, "url": "https://github.com/" + project,
    }


def saved_release(project, version="v1.0"):
    return {
        "platform": "github", "repo": project, "tag": version,
        "published": "2026-11-01T12:00:00Z", "release_id": version,
        "prerelease": False, "assets": [],
    }


class CatalogRefreshTests(unittest.TestCase):
    def test_reuse_cached_versions_and_only_check_new_repositories(self):
        repos = [github("dev/existing"), github("dev/new")]
        existing = saved_release("dev/existing")
        refresh = monitor.prepare_catalog_refresh(
            repos, {"github:dev/existing"},
            {"schema_version": 1, "releases": [existing]},
        )
        self.assertIsNotNone(refresh)
        scan, cached = refresh
        self.assertEqual([r["project"] for r in scan], ["dev/new"])
        self.assertEqual(cached, {"github:dev/existing": existing})

    def test_drop_removed_repos_without_adding_orphaned_releases(self):
        repos = [github("dev/existing")]
        state = {
            "schema_version": 1,
            "releases": [saved_release("dev/existing"), saved_release("dev/removed")],
        }
        scan, cached = monitor.prepare_catalog_refresh(
            repos, {"github:dev/existing", "github:dev/removed"}, state
        )
        self.assertEqual(scan, [])
        self.assertEqual(list(cached), ["github:dev/existing"])

    def test_unknown_or_invalid_cache_falls_back_to_full_scan(self):
        repos = [github("dev/existing"), github("dev/new")]
        for old_keys, snapshot in [
            (set(), {"schema_version": 1, "releases": []}),
            ({"github:dev/existing"}, {"schema_version": 999, "releases": []}),
            ({"github:dev/existing"}, {"schema_version": 1, "releases": {}}),
            ({"github:dev/existing"}, {"schema_version": 1, "releases": ["bad"]}),
        ]:
            self.assertIsNone(
                monitor.prepare_catalog_refresh(repos, old_keys, snapshot),
            )

    def test_catalog_dispatch_reuses_snapshot_without_upstream_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repos_file = root / "repos.txt"
            sync_file = root / "sync.json"
            state_file = root / "state.json"
            snapshot_file = root / "upstream_latest.json"
            history_file = root / "history.json"
            repos_file.write_text(
                "# generated\nhttps://github.com/dev/existing\n", encoding="utf-8"
            )
            state_file.write_text(
                json.dumps({"github:dev/existing": {
                    "tag": "v1.0", "release_id": "v1.0"
                }}),
                encoding="utf-8",
            )
            old = saved_release("dev/existing")
            snapshot_file.write_text(
                json.dumps({"schema_version": 1, "releases": [old]}),
                encoding="utf-8",
            )
            history_file.write_text("[]", encoding="utf-8")

            def catalog():
                repos_file.write_text(
                    "# generated\nhttps://github.com/dev/existing\n"
                    "https://github.com/dev/new\n", encoding="utf-8"
                )
                return [github("dev/existing"), github("dev/new")], {
                    "catalog_url": "https://example.test/catalog.json",
                    "repositories": 2,
                }

            def fetch(repo):
                if repo["project"] != "dev/new":
                    raise AssertionError("existing repo should be reused")
                return repo, saved_release("dev/new"), None

            env = {"PSVA_MONITOR_CATALOG_REFRESH": "1"}
            with (
                patch.dict(os.environ, env),
                patch.object(monitor, "REPOS_FILE", repos_file),
                patch.object(monitor, "STATE_FILE", state_file),
                patch.object(monitor, "UPSTREAM_LATEST_FILE", snapshot_file),
                patch.object(monitor, "SYNC_INFO_FILE", sync_file),
                patch.object(monitor, "HISTORY_FILE", history_file),
                patch.object(monitor, "sync_catalog_repositories", side_effect=catalog),
                patch.object(monitor, "fetch_latest_release", side_effect=fetch) as fetch_mock,
                patch.object(monitor, "scan_supported_folders", return_value=([], [])),
                patch.object(monitor, "generate_html"),
                patch.object(monitor, "send_discord", return_value=False),
            ):
                self.assertEqual(monitor.main(), 0)
            self.assertEqual(fetch_mock.call_count, 1)
            published = json.loads(snapshot_file.read_text(encoding="utf-8"))
            self.assertEqual({item["repo"] for item in published["releases"]},
                             {"dev/existing", "dev/new"})
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("github:dev/existing", state)
            self.assertIn("github:dev/new", state)
            info = json.loads(sync_file.read_text(encoding="utf-8"))
            self.assertEqual(info["scan_mode"], "catalog_refresh")
            self.assertEqual(info["cached_releases"], 1)
            self.assertEqual(info["successful_checks"], 1)


if __name__ == "__main__":
    unittest.main()
