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

    def test_vita3k_is_excluded_even_if_icon_exists(self):
        result, _, reason = dh.classify_target(
            "Vita3K", "Experimental PlayStation Vita emulator",
            "Vita3K emulator for Windows Linux macOS and Android", ["sce_sys/icon0.png"])
        self.assertEqual(result, "excluded")
        self.assertIn("Emulador", reason)

    def test_switch_recompilation_from_vita_is_excluded(self):
        result, _, _ = dh.classify_target(
            "gow-collection-nx",
            "God of War PS Vita recompiled for Nintendo Switch homebrew - libnx",
            "Compiles for the ARM64 Switch", [])
        self.assertEqual(result, "excluded")

    def test_vita_target_with_vpk_is_candidate(self):
        result, _, _ = dh.classify_target(
            "VitaCell", "Battery health homebrew for the PS Vita",
            "Installed on the PS Vita", [], {"url": "https://github.com/a/b.vpk"})
        self.assertEqual(result, "candidate")

    def test_homebrew_emulator_for_vita_is_not_vita_emulator(self):
        result, _, _ = dh.classify_target(
            "retro-emulator", "An emulator for PS Vita",
            "Install on PS Vita", ["sce_sys/icon0.png"])
        self.assertEqual(result, "candidate")

    def test_ps_vita_patch_is_kept_as_candidate(self):
        result, kind, _ = dh.classify_target(
            "English patch", "Translation patch for a PS Vita game",
            "Apply patch for PS Vita", [])
        self.assertEqual(result, "candidate")
        self.assertEqual(kind, "patch")

    def test_vita_kernel_plugin_without_vpk_is_candidate(self):
        result, kind, reason = dh.classify_target(
            "vita-coldboot",
            "PS Vita kernel plugin performing a cold reset on boot",
            "A taiHEN kernel plugin installed via ur0:tai and *KERNEL.",
            [])
        self.assertEqual(result, "candidate")
        self.assertEqual(kind, "plugin")
        self.assertIn("taiHEN", reason)

    def test_kernel_plugin_readme_sufficient_without_repo_description(self):
        status, kind, _ = dh.classify_target(
            "vita-coldboot", "",
            "A lightweight PS Vita kernel plugin for HENkaku Ensō. Install under *KERNEL in ur0:tai.", [])
        self.assertEqual((status, kind), ("candidate", "plugin"))

    def test_patch_database_is_not_misclassified_as_vita_patch(self):
        result, kind, _ = dh.classify_target(
            "PSX2PSP Reborn", "Native Rust POPS EBOOT.PBP builder for PSP & PS Vita, patch database",
            "", [])
        self.assertNotEqual(kind, "patch")
        self.assertNotEqual(result, "candidate")

    def test_psp_only_patch_remains_manual_review(self):
        result, _, _ = dh.classify_target(
            "PSP translation patch",
            "English patch for a PSP game. Play on PPSSPP, PSP or PS Vita.",
            "Apply the patch to a PSP ISO.", [])
        self.assertEqual(result, "review")

    def test_readme_vita_reference_alone_is_insufficient(self):
        result, _, _ = dh.classify_target(
            "OpenStrike", "A path-traced desktop FPS",
            "We used to discuss PS Vita rendering constraints.", [])
        self.assertEqual(result, "review")

    def test_existing_candidate_cleanup_preserves_audit(self):
        state = {"seen": {"gh:3": {"result": "candidate"}}}
        samples = {
            "gh:3": {"project": "Vita3K/Vita3K", "app_draft": {
                "name": "Vita3K", "description": "PlayStation Vita emulator",
                "long_description": "Emulator for Windows", "links": []}}
        }
        removed = dh.reclassify_existing(state, samples)
        self.assertEqual(removed, 1)
        self.assertEqual(samples, {})
        self.assertEqual(state["seen"]["gh:3"]["result"], "excluded")
        self.assertIn("gh:3", state["target_filter_audit"])

    def test_gitlab_queue_skips_unrelated_names_but_keeps_known_authors(self):
        state = {"seen": {}, "queue": [
            {"key": "gl:1", "platform": "gitlab",
             "repo": {"id": 1, "name": "civitas-core", "path_with_namespace": "civitas/civitas-core"}},
            {"key": "gl:2", "platform": "gitlab",
             "repo": {"id": 2, "name": "vita-port", "path_with_namespace": "newdev/vita-port"}},
            {"key": "gl:3", "platform": "gitlab",
             "repo": {"id": 3, "name": "unknown", "path_with_namespace": "trusteddev/unknown"}},
            {"key": "gh:4", "platform": "github",
             "repo": {"id": 4, "name": "example", "full_name": "any/example"}},
        ]}
        deleted = dh.prune_unrelated_gitlab_queue(state, ["gitlab:trusteddev"])
        self.assertEqual(deleted, 1)
        self.assertEqual(len(state["queue"]), 3)
        self.assertEqual(state["seen"]["gl:1"]["result"], "irrelevant_metadata")

    def test_catalog_sync_hides_registered_repo_and_keeps_other_discoveries(self):
        catalog = {"apps": [
            {"id": "vitacell", "name": "VitaCell", "title_id": "VITA00001",
             "author_ids": ["shadowmedicis"],
             "links": [{"type": "Repository",
                        "url": "https://github.com/shadowmedicis/VitaCell/releases/latest"}]},
        ]}
        official = dh.official_app_index(catalog)
        candidates = {
            "gh:42": {"project": "shadowmedicis/VitaCell",
                      "source_url": "https://github.com/shadowmedicis/VitaCell",
                      "app_draft": {"title_id": "", "name": "VitaCell", "links": []}},
            "gh:43": {"project": "notregistered/example",
                      "source_url": "https://github.com/notregistered/example",
                      "app_draft": {"name": "Example", "links": []}},
        }
        state = {"seen": {"gh:42": {"result": "candidate"}},
                 "queue": [{"key": "gh:44", "platform": "github",
                            "repo": {"id": 44, "full_name": "shadowmedicis/VitaCell"}},
                           {"key": "gh:45", "platform": "github",
                            "repo": {"id": 45, "full_name": "new/repo"}}]}
        removed = dh.reconcile_catalog(state, candidates, official)
        self.assertEqual(removed, 1)
        self.assertNotIn("gh:42", candidates)
        self.assertIn("gh:43", candidates)
        self.assertEqual(len(state["queue"]), 1)
        self.assertEqual(state["queue"][0]["key"], "gh:45")
        self.assertEqual(state["seen"]["gh:42"]["result"], "catalogued")
        self.assertIn("gh:42", state["catalogued_discoveries"])

    def test_catalog_id_or_exact_author_title_match_but_not_name_alone(self):
        official = dh.official_app_index([{
            "id": "vita-game", "name": "Great Vita Game", "title_id": "GREAT0001",
            "author_ids": ["dev1"], "links": []}])
        by_id = {"source_url": "https://github.com/other/a",
                 "app_draft": {"name": "Unrelated", "title_id": "GREAT0001"}}
        by_author = {"source_url": "https://github.com/other/b",
                     "app_draft": {"name": "Great-Vita-Game", "author_ids": ["dev1"]}}
        just_name = {"source_url": "https://github.com/other/c",
                     "app_draft": {"name": "Great Vita Game", "author_ids": ["dev2"]}}
        self.assertEqual(dh.match_official_candidate(by_id, official), "title_id:GREAT0001")
        self.assertEqual(dh.match_official_candidate(by_author, official), "same_author_and_title")
        self.assertIsNone(dh.match_official_candidate(just_name, official))

    def test_queued_known_vita_projects_take_priority_without_starving_rest(self):
        queue = [
            {"key": "gh:1", "platform": "github",
             "repo": {"id": 1, "full_name": "someone/unrelated-code",
                      "description": "A desktop CSS project"}},
            {"key": "gh:2", "platform": "github",
             "repo": {"id": 2, "full_name": "known-author/tool",
                      "owner": {"login": "known-author"}, "description": "Small tool"}},
            {"key": "gh:3", "platform": "github",
             "repo": {"id": 3, "full_name": "dev/ps-vita-player",
                      "description": "Homebrew for PS Vita"}},
        ]
        chosen = dh.pop_prioritized_task(queue, ["github:known-author"], 0)
        self.assertEqual(chosen["key"], "gh:3")
        low = dh.pop_prioritized_task(queue, ["github:known-author"], 4)
        self.assertEqual(low["key"], "gh:1")
        self.assertEqual(queue[0]["key"], "gh:2")

    def test_budget_enforced_without_network_call(self):
        with patch.object(dh, "REQUEST_COUNT", dh.MAX_REQUESTS):
            with self.assertRaises(dh.DiscoveryBudgetReached):
                dh.request("https://api.github.com/repos/example/project")

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
