"""Regression tests for PSVitaAlive compact catalog and tracker state safety."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
from catalog_schema_v2_compat import to_v1
from enrich_dashboard import catalog_repo_index


class CatalogCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://github.com/example/vita-homebrew"
        self.v1 = [{
            "id": "test-homebrew",
            "title_id": "TEST00001",
            "name": "Test Homebrew",
            "author_ids": ["example"],
            "category_id": "games",
            "version": "1.0",
            "links": [
                {"type": "Repository", "name": "Source", "url": self.url},
            ],
        }]
        self.v2 = [{
            "id": "test-homebrew",
            "tid": "TEST00001",
            "n": "Test Homebrew",
            "aids": ["example"],
            "cid": "games",
            "v": "1.0",
            "l": [
                {"t": "Repository", "n": "Source", "u": self.url},
            ],
        }]

    def test_v1_and_v2_discover_identical_repositories(self):
        self.assertEqual(to_v1(self.v2), self.v1)
        v1_repos = monitor.discover_repositories(self.v1)
        v2_repos = monitor.discover_repositories(to_v1(self.v2))
        self.assertEqual(v2_repos, v1_repos)
        self.assertEqual(len(v2_repos), 1)
        self.assertEqual(v2_repos[0]["project"], "example/vita-homebrew")

    def test_v1_and_v2_dashboard_index_are_identical(self):
        self.assertEqual(catalog_repo_index(to_v1(self.v2)), catalog_repo_index(self.v1))
        self.assertEqual(len(catalog_repo_index(to_v1(self.v2))), 1)

    def test_dynamic_fields_are_not_recursively_renamed(self):
        compact = [{
            "id": "example",
            "tids": {"US": "PCSE00001"},
            "srcs": {"u": {"t": "not-a-link"}},
            "l": [{"t": "Repository", "n": "Code", "u": self.url}],
        }]
        decoded = to_v1(compact)[0]
        self.assertEqual(decoded["title_ids"]["US"], "PCSE00001")
        self.assertEqual(decoded["sources"]["u"], {"t": "not-a-link"})
        self.assertEqual(decoded["links"][0]["url"], self.url)

    def test_empty_discovery_preserves_existing_repos_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repos_file = Path(directory) / "repos.txt"
            repos_file.write_text("existing data\n", encoding="utf-8")
            with patch.object(monitor, "request_json", return_value=[{
                "id": "no-repository", "tid": "TEST00002", "n": "Missing",
                "l": [{"t": "Download", "u": "https://example.org/homebrew.vpk"}],
            }]), patch.object(monitor, "REPOS_FILE", repos_file):
                with self.assertRaisesRegex(RuntimeError, "no contiene repositorios"):
                    monitor.sync_catalog_repositories()
            self.assertEqual(repos_file.read_text(encoding="utf-8"), "existing data\n")

    def test_large_drop_preserves_existing_repos_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repos_file = root / "repos.txt"
            repos_file.write_text("existing data\n", encoding="utf-8")
            sync_file = root / "sync.json"
            sync_file.write_text(json.dumps({"repositories": 800}), encoding="utf-8")
            state_file = root / "state.json"
            state_file.write_text("{}", encoding="utf-8")
            with patch.object(monitor, "request_json", return_value=self.v2), \
                 patch.object(monitor, "REPOS_FILE", repos_file), \
                 patch.object(monitor, "SYNC_INFO_FILE", sync_file), \
                 patch.object(monitor, "STATE_FILE", state_file):
                with self.assertRaisesRegex(RuntimeError, "Caída anómala"):
                    monitor.sync_catalog_repositories()
            self.assertEqual(repos_file.read_text(encoding="utf-8"), "existing data\n")

    def test_new_repositories_are_silently_baselined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_project = "example/known"
            new_project = "example/new"
            def make_repo(project):
                return {"platform": "github", "project": project,
                        "key": f"github:{project}", "url": f"https://github.com/{project}"}
            def fake_release(repo):
                return repo, {
                    "platform": "github", "repo": repo["project"],
                    "release_id": "123", "tag": "v2", "published": "2026-11-08T00:00:00Z",
                    "assets": [],
                }, None

            (root / "state.json").write_text(json.dumps({
                "github:example/known": {"tag": "v1", "release_id": "111"},
            }), encoding="utf-8")
            (root / "releases.json").write_text("[]", encoding="utf-8")
            (root / "sync.json").write_text("{}", encoding="utf-8")

            with patch.object(monitor, "sync_catalog_repositories", return_value=(
                [make_repo(old_project), make_repo(new_project)], {"repositories": 2}
            )), patch.object(monitor, "STATE_FILE", root / "state.json"), \
                 patch.object(monitor, "HISTORY_FILE", root / "releases.json"), \
                 patch.object(monitor, "SYNC_INFO_FILE", root / "sync.json"), \
                 patch.object(monitor, "fetch_latest_release", side_effect=fake_release), \
                 patch.object(monitor, "send_discord", return_value=True) as send, \
                 patch.object(monitor, "generate_html"):
                self.assertEqual(monitor.main(), 0)
                self.assertEqual(send.call_count, 1)
            current = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(current), 2)


if __name__ == "__main__":
    unittest.main()
