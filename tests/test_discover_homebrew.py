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

    def test_nested_vita_icon_and_screenshot_folders(self):
        repo = {"full_name": "artist/vita-title", "default_branch": "main"}
        files = [
            "third_party/example/icon.png",
            "src/app/game/sce_sys/icon0.png",
            "docs/guide/screens/01-menu.png",
            "docs/guide/screens/02-gameplay.webp",
            "game/assets/sprites/player.png",
        ]
        icon, shots = dh.media(repo, "github", files)
        self.assertTrue(icon.endswith("/src/app/game/sce_sys/icon0.png"))
        self.assertEqual(len(shots), 2)
        self.assertTrue(shots[0].endswith("/docs/guide/screens/01-menu.png"))
        self.assertTrue(shots[1].endswith("/docs/guide/screens/02-gameplay.webp"))

    def test_pic0_used_as_screenshot_only_when_no_real_capture(self):
        repo = {"full_name": "developer/sample", "default_branch": "main"}
        files = ["some/deep/source/sce_sys/icon0.png",
                 "some/deep/source/sce_sys/pic0.png",
                 "some/deep/source/sce_sys/livearea/contents/bg.png"]
        _, shots = dh.media(repo, "github", files)
        self.assertEqual(len(shots), 1)
        self.assertTrue(shots[0].endswith("/some/deep/source/sce_sys/pic0.png"))
        files.append("docs/screenshots/real-screen.png")
        _, shots = dh.media(repo, "github", files)
        self.assertEqual(len(shots), 1)
        self.assertTrue(shots[0].endswith("/docs/screenshots/real-screen.png"))

    def test_livearea_background_fallback_without_pic0(self):
        repo = {"full_name": "developer/sample", "default_branch": "main"}
        _, shots = dh.media(repo, "github", [
            "assets/vita/sce_sys/livearea/contents/bg.png"])
        self.assertEqual(len(shots), 1)
        self.assertTrue(shots[0].endswith("/assets/vita/sce_sys/livearea/contents/bg.png"))

    def test_only_confirmed_readme_embedded_images_become_screens(self):
        repo = {"full_name": "developer/sample", "default_branch": "master"}
        files = ["docs/images/01-diagnostic.png", "docs/images/02-analyse.png",
                 "docs/images/logo.png", "assets/fonts/font.png"]
        readme = ("![Gameplay](docs/images/01-diagnostic.png)\n"
                  "<img src=\"docs/images/02-analyse.png\">\n"
                  "![Made-up screenshot](docs/images/not-there.png)\n"
                  "![Escape](../../secrets.png)\n")
        _, shots = dh.media(repo, "github", files, readme)
        self.assertEqual(len(shots), 2)
        self.assertTrue(all("/docs/images/" in shot for shot in shots))
        self.assertFalse(any("not-there" in shot or "secrets" in shot for shot in shots))

    def test_badges_sprites_and_buttons_not_screenshots(self):
        repo = {"full_name": "developer/sample", "default_branch": "main"}
        icon, shots = dh.media(repo, "github", [
            "docs/screenshots/logo.png", "docs/screenshots/badge-build.png",
            "assets/sprites/enemy.png", "assets/ui/button-play.png",
            "docs/images/tutorial.png"])
        self.assertEqual((icon, shots), ("", []))

    def test_gitlab_tree_reads_all_pages_not_only_first_hundred(self):
        calls = []
        def fake_request(url, params=None, gitlab=False, plain=False):
            calls.append(params["page"])
            if params["page"] == 1:
                return [{"type": "blob", "path": "src/a/file"+str(i)+".txt"}
                        for i in range(100)]
            return [{"type": "blob", "path": "nested/game/sce_sys/icon0.png"}]
        with patch.object(dh, "request", side_effect=fake_request):
            paths = dh.files_for({"id": 9, "default_branch": "main"}, "gitlab")
        self.assertEqual(calls, [1, 2])
        self.assertIn("nested/game/sce_sys/icon0.png", paths)
        self.assertEqual(len(paths), 101)

    def test_github_truncated_tree_keeps_known_paths(self):
        with patch.object(dh, "request", return_value={
            "truncated": True, "tree": [
                {"type": "blob", "path": "deep/path/sce_sys/icon0.png"}]}):
            paths = dh.files_for({"full_name": "dev/app"}, "github")
        self.assertEqual(paths, ["deep/path/sce_sys/icon0.png"])

    def test_existing_draft_media_enrichment_preserves_original_facts(self):
        old = {"gh:14": {"platform": "github", "project": "dev/great-vita",
                        "key": "gh:14", "discovered_at": "2026-11-20T00:00:00+00:00",
                        "app_draft": {"name": "Great Vita", "description": "Original",
                                      "icon": "", "screenshots": [], "status": "",
                                      "title_id": ""},
                        "missing_fields": ["title_id", "icon", "status"]}}
        metadata = {"id": 14, "full_name": "dev/great-vita", "default_branch": "master"}
        filenames = ["deep/game/sce_sys/icon0.png",
                     "docs/preview/screenshots/01.png"]
        with patch.object(dh, "request", return_value=metadata), \
             patch.object(dh, "files_for", return_value=filenames):
            stats = dh.enrich_existing_media(old)
        item = old["gh:14"]
        self.assertEqual(stats["refreshed"], 1)
        self.assertEqual(stats["icons_added"], 1)
        self.assertEqual(stats["screenshots_added"], 1)
        self.assertTrue(item["app_draft"]["icon"].endswith("/deep/game/sce_sys/icon0.png"))
        self.assertEqual(len(item["app_draft"]["screenshots"]), 1)
        self.assertEqual(item["app_draft"]["description"], "Original")
        self.assertEqual(item["app_draft"]["status"], "")
        self.assertEqual(item["app_draft"]["title_id"], "")
        self.assertNotIn("icon", item["missing_fields"])
        self.assertEqual(item["media_scan_version"], dh.MEDIA_SCAN_VERSION)
        self.assertEqual(dh.enrich_existing_media(old)["refreshed"], 0)

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

    def test_forks_of_catalogued_d2vita_and_vampire_survivors_are_identified(self):
        catalog = dh.to_v1([
            {"id": "d2vita", "tid": "DTWO00001", "n": "D2Vita",
             "aids": ["franckrst"], "l": [{"t": "Repository",
             "u": "https://github.com/Franckrst/D2Vita"}]},
            {"id": "vampire-survivors", "tid": "VSPH00001", "n": "Vampire Survivors",
             "aids": ["federicoilgrandebardo"], "l": [{"t": "Repository",
             "u": "https://github.com/Federicoilgrandebardo/vampire-survivors-vita"}]},
        ])
        official = dh.official_app_index(catalog)
        forks = [
            ("Digital33Studios/D2Vita", "Franckrst/D2Vita", "D2Vita"),
            ("DrakonPL/vampire-survivors-vita",
             "Federicoilgrandebardo/vampire-survivors-vita", "vampire-survivors-vita"),
        ]
        candidates = {}
        for number, (candidate, origin, name) in enumerate(forks):
            api = {"fork": True, "full_name": candidate,
                   "parent": {"full_name": origin}, "source": {"full_name": origin}}
            evidence = dh.repository_lineage_from_api(api, "github")
            key = f"gh:{number+1}"
            candidates[key] = {"key": key, "platform": "github", "project": candidate,
                               "source_url": "https://github.com/" + candidate,
                               "app_draft": {"name": name, "title_id": "",
                                             "author_ids": [], "links": []},
                               **evidence}
            self.assertEqual(dh.match_official_candidate(candidates[key], official),
                             "fork_of:github:" + origin.casefold())
        state = {"seen": {}, "queue": []}
        self.assertEqual(dh.reconcile_catalog(state, candidates, official), 2)
        self.assertEqual(candidates, {})
        self.assertEqual(len(state["catalogued_discoveries"]), 2)

    def test_same_name_different_independent_project_is_not_removed(self):
        official = dh.official_app_index([{
            "id": "example", "name": "Example Vita",
            "author_ids": ["original"],
            "links": [{"type": "Repository", "url": "https://github.com/original/vita-game"}]}])
        entry = {"project": "other/vita-game",
                 "source_url": "https://github.com/other/vita-game",
                 "app_draft": {"name": "Example Vita", "author_ids": ["other"], "links": []},
                 "lineage_is_fork": False, "lineage_parent": "", "lineage_source": ""}
        self.assertIsNone(dh.match_official_candidate(entry, official))

    def test_fork_source_is_checked_even_when_parent_differs(self):
        official = dh.official_app_index([{
            "id": "vita-game", "name": "Vita Game",
            "links": [{"url": "https://github.com/author/vita-game"}]}])
        grandchild = {"source_url": "https://github.com/third/fork",
                      "app_draft": {"name": "Renamed Game", "links": []},
                      "lineage_is_fork": True,
                      "lineage_parent": "github:second/fork",
                      "lineage_source": "github:author/vita-game"}
        self.assertEqual(dh.match_official_candidate(grandchild, official),
                         "fork_of:github:author/vita-game")

    def test_existing_fork_ancestry_check_is_bounded_and_persistent(self):
        official = dh.official_app_index([{
            "id": "vita-game", "name": "Vita Game",
            "links": [{"url": "https://github.com/author/vita-game"}]}])
        candidates = {"gh:42": {
            "key": "gh:42", "platform": "github", "project": "third/fork",
            "app_draft": {"name": "Vita Game", "links": []}}}
        info = {"lineage_is_fork": True, "lineage_source": "github:author/vita-game",
                "lineage_parent": "github:second/fork",
                "lineage_check_version": dh.LINEAGE_SCAN_VERSION}
        with patch.object(dh, "fetch_repository_lineage", return_value=info) as fetch:
            stats = dh.enrich_existing_lineage(candidates, official)
            self.assertEqual(stats["checked"], 1)
            self.assertEqual(dh.match_official_candidate(candidates["gh:42"], official),
                             "fork_of:github:author/vita-game")
            self.assertEqual(dh.enrich_existing_lineage(candidates, official)["checked"], 0)
            self.assertEqual(fetch.call_count, 1)

    def test_new_candidate_fork_is_filtered_before_entering_discovery(self):
        official = dh.official_app_index([{
            "id": "vita-game", "name": "Example Vita",
            "links": [{"url": "https://github.com/original/vita-game"}]}])
        repo = {"id": 77, "name": "vita-game", "full_name": "other/vita-game",
                "fork": True, "description": "A PS Vita homebrew",
                "default_branch": "main"}
        evidence = {"lineage_is_fork": True, "lineage_source": "github:original/vita-game",
                    "lineage_parent": "github:original/vita-game",
                    "lineage_check_version": dh.LINEAGE_SCAN_VERSION}
        with patch.object(dh, "readme", return_value="For PS Vita"), \
             patch.object(dh, "files_for", return_value=["sce_sys/icon0.png"]), \
             patch.object(dh, "find_vpk", return_value={}), \
             patch.object(dh, "fetch_repository_lineage", return_value=evidence):
            self.assertEqual(dh.analyze(repo, "github", official["repos"], {},
                                       official), ("catalogued", None))

    def test_original_repo_can_match_when_fork_flag_is_false(self):
        evidence = dh.repository_lineage_from_api(
            {"fork": False, "source": {"full_name": "author/other"}}, "github")
        self.assertFalse(evidence["lineage_is_fork"])
        self.assertEqual(evidence["lineage_source"], "")

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

    def test_priority_skips_attempted_repositories_this_round(self):
        queue = [
            {"key": "gh:1", "platform": "github", "repo": {
                "id": 1, "full_name": "dev/psvita-first", "description": "PS Vita port"}},
            {"key": "gh:2", "platform": "github", "repo": {
                "id": 2, "full_name": "dev/psvita-next", "description": "PS Vita port"}},
        ]
        chosen = dh.pop_prioritized_task(queue, [], 0, {"gh:1"})
        self.assertEqual(chosen["key"], "gh:2")
        self.assertEqual(len(queue), 1)
        self.assertIsNone(dh.pop_prioritized_task(queue, [], 0, {"gh:1"}))

    def test_empty_github_git_tree_is_treated_as_no_files(self):
        response = type("Reply", (), {
            "status_code": 409, "headers": {},
        })()
        with patch.object(dh.HTTP, "get", return_value=response):
            with patch.object(dh, "REQUEST_COUNT", 0), patch.object(dh, "LOW_RATE_LIMIT", False):
                self.assertEqual(dh.request(
                    "https://api.github.com/repos/example/empty/git/trees/main"), {"tree": []})

    def test_budget_enforced_without_network_call(self):
        with patch.object(dh, "REQUEST_COUNT", dh.MAX_REQUESTS):
            with self.assertRaises(dh.DiscoveryBudgetReached):
                dh.request("https://api.github.com/repos/example/project")

    def test_author_filter_groups_case_insensitively_and_counts_projects(self):
        entries = [
            {"project": "DevAlpha/one", "platform": "github",
             "author_profile": "https://github.com/DevAlpha"},
            {"project": "devalpha/two", "platform": "github",
             "author_profile": "https://github.com/devalpha"},
            {"project": "DevAlpha/three", "platform": "gitlab",
             "author_profile": "https://gitlab.com/DevAlpha"},
            {"project": "Other/four", "platform": "github",
             "author_profile": "https://github.com/Other"},
        ]
        self.assertEqual(dash.author_identity(entries[0]), ("github:devalpha", "DevAlpha"))
        self.assertEqual(dash.author_identity(entries[1]), ("github:devalpha", "devalpha"))
        self.assertEqual(dash.author_identity(entries[2]), ("gitlab:devalpha", "DevAlpha"))
        options = dash.author_options(entries)
        self.assertIn('<option value="github:devalpha">DevAlpha · GitHub (2)</option>', options)
        self.assertIn('<option value="gitlab:devalpha">DevAlpha · GitLab (1)</option>', options)
        self.assertEqual(options.count('value="github:devalpha"'), 1)
        self.assertLess(options.index('value="github:devalpha"'),
                        options.index('value="gitlab:devalpha"'))

    def test_author_filter_uses_existing_project_name_as_fallback(self):
        entry = {"project": "Known-Dev/project", "platform": "github",
                 "author_profile": ""}
        self.assertEqual(dash.author_identity(entry), ("github:known-dev", "Known-Dev"))
        self.assertEqual(dash.author_identity({"platform": "github", "project": "invalid"}), ("", ""))

    def test_author_filter_rejects_unsafe_author_identity(self):
        malicious = {"project": "user<script>/project", "platform": "github",
                     "author_profile": "https://github.com/user<script>"}
        self.assertEqual(dash.author_identity(malicious), ("", ""))
        wrong_host = {"project": "safe/repo", "platform": "github",
                      "author_profile": "https://gitlab.com/someone"}
        self.assertEqual(dash.author_identity(wrong_host), ("github:safe", "safe"))

    def test_dashboard_author_filter_is_combined_with_other_filters(self):
        entries = {
            "gh:11": {"key": "gh:11", "platform": "github", "project": "Dev/one",
                      "author_profile": "https://github.com/Dev",
                      "app_draft": {"name": "Homebrew One", "screenshots": []}},
            "gh:12": {"key": "gh:12", "platform": "github", "project": "Dev/two",
                      "author_profile": "https://github.com/Dev",
                      "app_draft": {"name": "Homebrew Two", "screenshots": []}},
        }
        output = dash.render(entries, {})
        self.assertIn('id="author"', output)
        self.assertIn('id="visible-count"', output)
        self.assertIn('<option value="github:dev">Dev · GitHub (2)</option>', output)
        self.assertEqual(output.count('data-author="github:dev"'), 2)
        self.assertEqual(output.count('data-select-author="github:dev"'), 2)
        self.assertIn("card.dataset.author===author.value", output)
        self.assertIn("card.dataset.platform===platform.value", output)
        self.assertIn("card.dataset.media===media.value", output)
        self.assertIn("search.value.trim().toLowerCase()", output)
        self.assertIn("window.location.search", output)
        self.assertIn("Ver proyectos del autor", output)
        self.assertNotIn("__AUTHORS__", output)
        self.assertIn("la autoría definitiva se verifica", output)

    def test_dashboard_labels_confirmed_forks_and_links_original(self):
        example = {
            "key": "gh:1461905208", "project": "edgarlugo/PSVitaAlive",
            "platform": "github", "source_url": "https://github.com/edgarlugo/PSVitaAlive",
            "lineage_check_version": dh.LINEAGE_SCAN_VERSION,
            "lineage_is_fork": True,
            "lineage_source": "github:vegettosan/psvitaalive",
            "lineage_parent": "github:vegettosan/psvitaalive",
            "app_draft": {"name": "PSVitaAlive", "screenshots": []}
        }
        out = dash.render({"gh:1461905208": example}, {})
        self.assertIn('data-fork="yes"', out)
        self.assertIn('<span class="tag-fork"', out)
        self.assertIn('Fork de:', out)
        self.assertIn('href="https://github.com/vegettosan/psvitaalive"', out)
        self.assertIn('Repositorio original del fork', out)
        self.assertIn('Forks identificados: <strong>1</strong>', out)
        self.assertIn('id="fork"', out)
        self.assertIn('card.dataset.fork===fork.value', out)

    def test_dashboard_does_not_mark_independent_repo_as_fork(self):
        normal = {"key": "gh:1", "platform": "github",
                  "project": "dev/native-homebrew",
                  "lineage_is_fork": False,
                  "app_draft": {"name": "Native Vita", "screenshots": []}}
        html = dash.render({"gh:1": normal}, {})
        self.assertIn('data-fork="no"', html)
        self.assertNotIn('<span class="tag-fork"', html)
        self.assertNotIn('Fork de:', html)
        self.assertIn('Forks identificados: <strong>0</strong>', html)

    def test_fork_without_origin_still_has_label(self):
        unknown = {"key": "gh:2", "platform": "github",
                   "project": "dev/fork", "lineage_is_fork": True,
                   "lineage_source": "", "lineage_parent": "",
                   "app_draft": {"name": "Test", "screenshots": []}}
        html = dash.render({"gh:2": unknown}, {})
        self.assertIn('data-fork="yes"', html)
        self.assertIn('<span class="tag-fork"', html)
        self.assertIn('origen no disponible', html)
        self.assertNotIn('Repositorio original del fork</a>', html)

    def test_fork_origin_rejects_untrusted_link_and_html(self):
        entry = {"lineage_is_fork": True,
                 "lineage_source": "github:evil<script>/bad",
                 "lineage_parent": "github:other/../../danger"}
        self.assertEqual(dash.fork_origin(entry), ("", ""))
        entry["lineage_parent"] = "gitlab:valid-team/psvita/app"
        self.assertEqual(dash.fork_origin(entry),
                         ("valid-team/psvita/app",
                          "https://gitlab.com/valid-team/psvita/app"))
        entry["lineage_is_fork"] = False
        self.assertEqual(dash.fork_origin(entry), ("", ""))

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
