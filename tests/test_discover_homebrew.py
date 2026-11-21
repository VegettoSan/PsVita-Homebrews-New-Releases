"""Offline regression tests for the independent Homebrew discovery scanner."""
import unittest
from unittest.mock import patch

import discover_homebrew as dh
import homebrew_discovery_dashboard as dash


class TextLimitsTests(unittest.TestCase):
    def test_short_description_is_bounded(self):
        self.assertEqual(dh.short_text("A PS Vita game.", 180), "A PS Vita game.")
        self.assertEqual(dh.short_text("x" * 1200, 180), "")
        self.assertLessEqual(len(dh.short_text("Good Vita app. " + "a" * 3000, 180)), 180)

    def test_readme_skips_long_readmes_and_badges(self):
        readme = "# Project\n\n![badge](badge.png)\n\n" + ("massive " * 400) + "\n\nA PS Vita port of a small puzzle game.\n"
        self.assertEqual(dh.readme_paragraph(readme), "A PS Vita port of a small puzzle game.")
        self.assertLessEqual(len(dh.readme_paragraph(readme)), 420)

    def test_empty_long_description_allowed(self):
        repo = {"id": 15, "name": "vita-test", "full_name": "creator/vita-test",
                "description": "A small PS Vita port.", "default_branch": "main",
                "owner": {"login": "creator"}}
        with patch.object(dh, "readme", return_value=""), patch.object(dh, "files_for", return_value=["sce_sys/icon0.png"]), patch.object(dh, "find_vpk", return_value={}):
            result, item = dh.analyze(repo, "github", set(), {})
        self.assertEqual(result, "candidate")
        self.assertEqual(item["app_draft"]["description"], "A small PS Vita port.")
        self.assertEqual(item["app_draft"]["long_description"], "")
        self.assertEqual(item["app_draft"]["title_id"], "")
        self.assertEqual(item["app_draft"]["author_ids"], [])
        self.assertEqual(item["review_state"], "new")

    def test_author_profile_does_not_infer_from_search_url(self):
        people, matches = dh.profiles_from_authors([
            {"id": "known", "links": [{"url": "https://github.com/known"}]},
            {"id": "uncertain", "links": [{"url": "https://github.com/search?q=unknown"}]},
        ])
        self.assertEqual(people, ["github:known"])
        self.assertEqual(matches["github:known"], ["known"])

    def test_catalog_repositories_prevent_duplicates(self):
        canonical = [{"links": [{"url": "https://github.com/example/old-vita/releases/latest"}]}]
        self.assertIn("github:example/old-vita", dh.official_repo_keys(canonical))
        repo = {"id": 16, "name": "old-vita", "full_name": "example/old-vita", "description": "PS Vita"}
        self.assertEqual(dh.analyze(repo, "github", dh.official_repo_keys(canonical), {})[0], "catalogued")

    def test_queue_is_persistent_and_deduplicates(self):
        state = {"seen": {}, "queue": []}
        repo = {"id": 123, "full_name": "example/project"}
        dh.enqueue(state, repo, "github")
        dh.enqueue(state, repo, "github")
        self.assertEqual(len(state["queue"]), 1)
        state["seen"]["gh:123"] = {"result": "unrelated"}
        dh.enqueue(state, repo, "github")
        self.assertEqual(len(state["queue"]), 1)

    def test_media_paths_are_from_verified_tree(self):
        repo = {"id": 11, "name": "ps-vita-example", "full_name": "author/ps-vita-example", "default_branch": "main"}
        icon, shots = dh.media(repo, "github", ["sce_sys/icon0.png", "screenshots/001.png", "README.md"])
        self.assertTrue(icon.endswith("/sce_sys/icon0.png"))
        self.assertEqual(len(shots), 1)

    def test_dashboard_escapes_untrusted_metadata(self):
        html = dash.render({"gh:1": {"key": "gh:1", "project": "a/b", "platform": "github",
                                    "source_url": "https://github.com/a/b", "score": 5,
                                    "app_draft": {"name": "<script>alert(1)</script>",
                                                  "description": "PS Vita", "screenshots": []},
                                    "missing_fields": ["title_id"]}}, {})
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="discovery-drafts/gh_1.json"', html)


if __name__ == "__main__":
    unittest.main()
