"""Offline regression tests for the independent Game/Data Files discovery."""
import unittest
import csv
import io
import community_port_sheet as sheet
from unittest.mock import patch
from catalog_schema_v2_compat import to_v1
import discover_game_data as gd
import game_data_dashboard as ui


def port(app_id="port-one", links=None, extra=None, category="ports"):
    app = {"id": app_id, "name": app_id.title(), "category_id": category,
           "title_id": "PORT12345", "links": links or []}
    app.update(extra or {})
    return app


class DiscoveryTests(unittest.TestCase):
    def test_only_ports_without_existing_data_are_selected(self):
        catalog = [
            port("without-data", extra={
                "requirements": "You need to copy your original game assets.",
                "links": [{"type": "Repository", "url": "https://github.com/dev/game"}],
            }),
            port("with-game", links=[{"type": "Game Files", "url": "https://files.example/zip"}]),
            port("with-data", links=[{"type": "Data Files", "url": "https://files.example/data"}]),
            port("legacy-name", links=[{"type": "Download", "name": "Game Files", "url": "https://files.example/data"}]),
            port("legacy-url", extra={"data_url": "https://files.example/data"}),
            port("not-a-port", category="games"),
        ]
        result = gd.missing_ports(catalog)
        self.assertEqual([a["id"] for a in result], ["without-data"])
        self.assertEqual(result[0]["repositories"], [["github", "dev/game"]])
        self.assertIn("menciona archivos externos", result[0]["reason"])

    def test_compact_catalog_fields_supported(self):
        compact = [{"id": "old", "n": "Old", "cid": "ports",
                    "tid": "PORT10000", "l": [{"t": "Download", "n": "VPK",
                                              "u": "https://github.com/x/y"}]}]
        self.assertEqual(gd.missing_ports(to_v1(compact))[0]["name"], "Old")

    def test_guard_against_untrusted_and_incorrect_repos(self):
        self.assertIsNone(gd.repo_from_url("http://github.com/example/app"))
        self.assertIsNone(gd.repo_from_url("https://github.com@evil.com/a/b"))
        self.assertEqual(gd.repo_from_url("https://gitlab.com/a/b/-/releases"),
                         ("gitlab", "a/b"))
        self.assertFalse(gd.valid_page("https://github.com/a/other/releases", "github", "a/b"))

    def test_source_discovery_is_release_page_only(self):
        asset = {"tag_name": "v2", "html_url": "https://github.com/dev/game/releases/tag/v2",
                 "assets": [{"name": "game-data.zip"}], "body": "Release notes",
                 "published_at": "2026-11-16T12:00:00Z"}
        with patch.object(gd, "get_json", return_value=[asset]):
            hits = gd.search_project("github", "dev/game")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "possible-archive")
        self.assertEqual(hits[0]["url"], asset["html_url"])

    def test_removed_when_data_added_and_new_results_are_deduplicated(self):
        previous = {"schema_version": 1, "apps": [
            {"id": "port-one", "checked_at": "2026-11-15",
             "candidates": [{"url": "https://github.com/dev/game/releases/tag/old",
                             "kind": "possible-archive",
                             "source": "GitHub Releases", "title": "Posible paquete: datafiles.zip"}]}]}
        changed = [{
            "id": "port-one", "name": "Port One", "category_id": "ports",
            "links": [{"type": "Repository", "url": "https://github.com/dev/game"}]
        }]
        def fake_scanner(_):
            return ([{"url": "https://github.com/dev/game/releases/tag/new",
                      "kind": "possible-archive",
                      "source": "GitHub Releases", "title": "Posible paquete: game-data.zip"}], [])
        report, new = gd.build_report(changed, previous, scanner=fake_scanner,
                                      timestamp="2026-11-16T12:00:00Z")
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["new_matches"], 1)
        self.assertEqual(len(new), 1)
        changed[0]["links"].append({"type": "Game Files", "url": "https://example.org/files.zip"})
        report, new = gd.build_report(changed, report, scanner=fake_scanner)
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(new, [])

    def test_generic_issue_and_release_instructions_are_hidden(self):
        release = {"tag_name": "v2",
                   "html_url": "https://github.com/dev/game/releases/tag/v2",
                   "assets": [{"name": "game.vpk"}],
                   "body": "You need to copy original data files"}
        with patch.object(gd, "get_json", return_value=[release]):
            hits = gd.search_project("github", "dev/game")
        self.assertEqual(hits, [])
        self.assertFalse(gd.is_strong_candidate({
            "kind": "community-reference", "source": "GitHub Issues",
            "title": "Game crashes when loading data files",
            "url": "https://github.com/dev/game/issues/4",
        }))
        old = {"apps": [{
            "id": "port-one", "candidates": [{
                "kind": "instructions", "source": "GitHub Releases",
                "url": "https://github.com/dev/game/releases/tag/v1",
            }]
        }]}
        report, _ = gd.build_report([
            port("port-one", links=[
                {"type": "Repository", "url": "https://github.com/dev/game"}
            ])
        ], old, scanner=lambda _: ([], []), sheet_rows=[])
        self.assertEqual(report["possible_matches"], 0)
        self.assertEqual(report["apps"][0]["candidates"], [])

    def test_sheet_csv_filters_missing_and_retains_linked_packages(self):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Cover", "Game Name", "Download Link",
                         "GitHub / Developer Page", "Development Stage",
                         "Last Verified By", "Last Verified Date"])
        writer.writerow(["", "Test Game Vita",
                         "https://www.mediafire.com/file/ab/datafiles.zip/file",
                         "https://github.com/dev/test-game", "Release",
                         "u/Tester", "2026-11-15"])
        writer.writerow(["", "Missing Game", "Missing",
                         "https://github.com/dev/missing", "Release", "", ""])
        writer.writerow(["", "Vpk Only", "https://github.com/dev/vpk/releases/foo.vpk",
                         "https://github.com/dev/vpk", "Release", "", ""])
        writer.writerow(["", "Spoofed", "https://www.mediafire.com/file/a/data.zip",
                         "https://github.com@evil.example/dev/test-game", "Release", "", ""])
        rows = sheet.parse_sheet_csv(buffer.getvalue())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["projects"], [("github", "dev/test-game")])
        self.assertEqual(rows[0]["archive_hosts"], ["www.mediafire.com"])
        self.assertIn("range=A2", rows[0]["url"])
        self.assertEqual(rows[1]["evidence_type"], "bundle")
        self.assertEqual(rows[1]["name"], "Vpk Only")
        self.assertEqual(rows[2]["projects"], [])
        apps = [port("test-game", extra={
            "name": "Test Game Vita",
            "links": [{"type": "Repository", "url": "https://github.com/dev/test-game"}]
        })]
        found = sheet.match_apps(gd.missing_ports(apps), rows)
        self.assertEqual(len(found["test-game"]), 1)
        self.assertEqual(found["test-game"][0]["kind"], "community-sheet")
        self.assertTrue(gd.is_strong_candidate(found["test-game"][0]))

    def test_sheet_ignores_ambiguous_shared_repositories(self):
        row = {"name": "Example One", "row": 2,
               "projects": [("github", "dev/shared")],
               "archive_hosts": ["www.mediafire.com"],
               "url": sheet.SHEET_URL + "&range=A2",
               "verified_by": "", "verified_date": ""}
        apps = [
            {"id": "example-one", "name": "Example One",
             "repositories": [["github", "dev/shared"]]},
            {"id": "example-two", "name": "Example Two",
             "repositories": [["github", "dev/shared"]]},
        ]
        matched = sheet.match_apps(apps, [row])
        self.assertEqual(set(matched), {"example-one"})
        self.assertEqual(matched["example-one"][0]["match_method"], "repository-title")

    def test_sheet_matches_unique_repo_with_alternate_game_title(self):
        apps = [{"id": "bc2-vita", "name": "BC2 Vita",
                 "repositories": [["github", "dev/battlefield-bc2"]]}]
        row = {"name": "Battlefield: Bad Company 2 (Android)", "row": 15,
               "projects": [("github", "dev/battlefield-bc2")],
               "archive_hosts": ["www.mediafire.com"],
               "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A15",
               "verified_by": "", "verified_date": ""}
        found = sheet.match_apps(apps, [row])
        self.assertEqual(found["bc2-vita"][0]["match_method"], "repository")
        self.assertEqual(found["bc2-vita"][0]["evidence_type"], "bundle")
        self.assertIn("datos anunciados", found["bc2-vita"][0]["title"])

    def test_sheet_shared_repository_does_not_mix_unrelated_titles(self):
        apps = [{"id": "engine", "name": "vitaXash3D",
                 "repositories": [["github", "dev/shared"]]}]
        rows = []
        for i, game in enumerate(["Half-Life", "Half-Life: Blue Shift"], 2):
            rows.append({"name": game, "row": i,
                         "projects": [("github", "dev/shared")],
                         "archive_hosts": ["www.mediafire.com"],
                         "evidence_type": "bundle",
                         "url": sheet.SHEET_URL + "&range=A" + str(i),
                         "verified_by": "", "verified_date": ""})
        self.assertEqual(sheet.match_apps(apps, rows), {})

    def test_sheet_title_fallback_is_exact_and_rejects_different_repos(self):
        row = {"name": "Unique Game Vita", "row": 6,
               "projects": [], "archive_hosts": ["archive.org"],
               "evidence_type": "archive",
               "url": sheet.SHEET_URL + "&range=A6",
               "verified_by": "", "verified_date": ""}
        apps = [{"id": "unique", "name": "Unique Game",
                 "repositories": []}]
        found = sheet.match_apps(apps, [row])
        self.assertEqual(found["unique"][0]["match_method"], "exact-title")
        self.assertEqual(sheet.match_apps(apps + [
            {"id": "duplicate", "name": "Unique Game Vita", "repositories": []}
        ], [row]), {})
        row["projects"] = [("github", "different/developer")]
        apps[0]["repositories"] = [["github", "right/developer"]]
        self.assertEqual(sheet.match_apps(apps, [row]), {})

    def test_download_release_links_are_considered_repo_sources(self):
        app = port("download-only", links=[{
            "type": "Download",
            "url": "https://github.com/example/project/releases/download/v1/main.vpk",
        }])
        self.assertEqual(gd.app_repos(app), [("github", "example/project")])

    def test_scan_limit_covers_current_catalog(self):
        self.assertGreaterEqual(gd.SCAN_LIMIT, 480)

    def test_description_alias_matches_different_catalog_name(self):
        app = port("goldenidol", extra={"name": "GoldenIdol-Vita"})
        row = {"name": "The Case of The Golden Idol", "row": 2,
               "projects": [], "archive_hosts": ["www.mediafire.com"],
               "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A2",
               "verified_by": "", "verified_date": ""}
        found = sheet.match_apps(gd.missing_ports([app]), [row], {
            "goldenidol": {
                "description": "A PS Vita port of The Case of the Golden Idol.",
                "long_description": "",
            }
        })
        self.assertEqual(found["goldenidol"][0]["match_method"], "short-description")
        self.assertEqual(found["goldenidol"][0]["sheet_title"],
                         "The Case of The Golden Idol")

    def test_long_description_alias_and_platform_suffix(self):
        app = port("bc2", extra={"name": "BC2 Vita"})
        row = {"name": "Battlefield: Bad Company 2 (Android)", "row": 7,
               "projects": [], "archive_hosts": ["archive.org"],
               "evidence_type": "archive",
               "url": sheet.SHEET_URL + "&range=A7",
               "verified_by": "", "verified_date": ""}
        descriptions = {"bc2": {
            "description": "New PS Vita homebrew game.",
            "long_description": "This is a wrapper/port of Battlefield: Bad Company 2 Mobile for PS Vita."
        }}
        found = sheet.match_apps(gd.missing_ports([app]), [row], descriptions)
        self.assertEqual(found["bc2"][0]["match_method"], "long-description")

    def test_mentions_and_comparisons_do_not_match(self):
        row = {"name": "Half-Life", "row": 4, "projects": [],
               "archive_hosts": ["archive.org"], "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A4",
               "verified_by": "", "verified_date": ""}
        apps = [port("some-engine", extra={"name": "Source Engine Demo"})]
        for words in ("Inspired by Half-Life and Quake",
                      "Similar to Half-Life.",
                      "Requires Half-Life data to run.",
                      "Works with Half-Life files.",
                      "This application supports Half-Life and Counter-Strike."):
            found = sheet.match_apps(gd.missing_ports(apps), [row],
                                     {"some-engine": {"description": words}})
            self.assertEqual(found, {}, msg=words)

    def test_ambiguous_description_matches_are_rejected(self):
        row = {"name": "Quake II", "row": 8,
               "projects": [], "archive_hosts": ["www.mediafire.com"],
               "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A8",
               "verified_by": "", "verified_date": ""}
        apps = [
            port("one", extra={"name": "Source Port One"}),
            port("two", extra={"name": "Source Port Two"})
        ]
        details = {a["id"]: {"description": "Port of Quake II for PS Vita."}
                   for a in apps}
        self.assertEqual(sheet.match_apps(gd.missing_ports(apps), [row], details), {})

    def test_description_cannot_override_other_repositories_identity(self):
        row = {"name": "Some Classic Game", "row": 9,
               "projects": [("github", "dev/another-app")],
               "archive_hosts": ["www.mediafire.com"], "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A9",
               "verified_by": "", "verified_date": ""}
        apps = [
            port("one", extra={"name": "First",
                               "links": [{"type": "Repository",
                                          "url": "https://github.com/dev/first"}]}),
            port("two", extra={"name": "Second",
                               "links": [{"type": "Repository",
                                          "url": "https://github.com/dev/another-app"}]})
        ]
        details = {"one": {"description": "Port of Some Classic Game"}}
        self.assertEqual(sheet.match_apps(gd.missing_ports(apps), [row], details),
                         {"two": sheet.match_apps(gd.missing_ports(apps), [row], details)["two"]})
        # The sheet's explicit repository must never select the other port.
        self.assertNotIn("one", sheet.match_apps(gd.missing_ports(apps), [row], details))

    def test_report_uses_descriptions_without_serializing_them(self):
        app = port("alias", extra={
            "name": "Short Internal Name",
            "description": "Port of Totally Different Game",
            "long_description": "Historical metadata and lots of text.",
        })
        row = {"name": "Totally Different Game", "row": 11,
               "projects": [], "archive_hosts": ["archive.org"],
               "evidence_type": "bundle",
               "url": sheet.SHEET_URL + "&range=A11",
               "verified_by": "", "verified_date": ""}
        report, _ = gd.build_report([app], {}, scanner=lambda _: ([], []),
                                    sheet_rows=[row])
        self.assertEqual(report["possible_matches"], 1)
        candidate = report["apps"][0]["candidates"][0]
        self.assertEqual(candidate["match_method"], "short-description")
        self.assertNotIn("description", report["apps"][0])
        self.assertNotIn("long_description", report["apps"][0])

    def test_sheet_failure_preserves_old_community_evidence(self):
        record = {
            "id": "port-one", "candidates": [{
                "source": "PS Vita Ports Tracker (Reddit / Google Sheets)",
                "kind": "community-sheet",
                "title": "Hoja comunitaria: paquete referenciado",
                "url": sheet.SHEET_URL + "&range=A22",
            }]
        }
        app = port("port-one", links=[
            {"type": "Repository", "url": "https://github.com/dev/game"}
        ])
        report, _ = gd.build_report(
            [app], {"apps": [record]}, scanner=lambda _: ([], []),
            sheet_rows=None, sheet_error="HTTPError")
        self.assertEqual(report["sheet_status"], "error")
        self.assertEqual(report["possible_matches"], 1)

    def test_game_data_never_sends_discord_notifications(self):
        from pathlib import Path
        source = Path(gd.__file__).read_text(encoding="utf-8")
        workflow = Path(".github/workflows/discover_game_data.yml").read_text(encoding="utf-8")
        self.assertNotIn("DISCORD_WEBHOOK_URL", source)
        self.assertNotIn("DISCORD_WEBHOOK_URL", workflow)
        self.assertNotIn("requests.post(", source)
        self.assertNotIn("def notify(", source)

    def test_scan_failure_preserves_previous_evidence(self):
        app = port("port-one", links=[
            {"type": "Repository", "url": "https://github.com/dev/game"}
        ])
        prior = {"apps": [{"id": "port-one", "checked_at": "2026-11-15",
                           "candidates": [{"url": "https://github.com/dev/game/releases/tag/a",
                                          "kind": "possible-archive",
                                          "source": "GitHub Releases",
                                          "title": "Posible paquete: game-data.zip"}]}]}
        report, new = gd.build_report([app], prior,
                    scanner=lambda a: ([], ["API throttled"]))
        self.assertEqual(len(report["apps"][0]["candidates"]), 1)
        self.assertEqual(report["apps"][0]["checked_at"], "2026-11-15")
        self.assertEqual(new, [])


class DashboardTests(unittest.TestCase):
    DOC = ('<html><head><style></style></head><body>'
           '<div class="wrap toolbar"></div><main class="wrap" id="releases">'
           'Release content</main><footer class="wrap"></footer></body></html>')

    def test_navigation_keeps_original_release_tab(self):
        html = ui.add_data_tab(self.DOC, {
            "scanned_at": "2026-11-16T12:00:00Z", "new_matches": 0,
            "apps": [{"id": "sample", "title_id": "SAMP00001", "name": "Sample",
                      "reason": "No links", "repositories": [],
                      "candidates": []}]
        })
        self.assertIn('id="gd-release-tab"', html)
        self.assertIn('id="gd-data-tab"', html)
        self.assertIn('id="gd-panel"', html)
        self.assertIn('id="gd-search"', html)
        self.assertNotIn('<article class="gd-item"', html)
        self.assertIn('no hay referencias con evidencia suficiente', html)
        self.assertIn('id="releases">Release content', html)
        self.assertIn("location.hash === '#game-data'", html)

    def test_only_evidence_cards_are_shown_and_filter_has_no_missing(self):
        report = {"scanned_at": "2026-11-16T12:00:00Z", "apps": [
            {"id": "none", "name": "No evidence", "candidates": []},
            {"id": "real", "name": "Real evidence",
             "repositories": [],
             "candidates": [{
                 "source": "GitHub Releases", "kind": "possible-archive",
                 "title": "Posible paquete: datafiles.zip",
                 "url": "https://github.com/dev/game/releases/tag/v2",
             }]}
        ]}
        markup = ui.add_data_tab(self.DOC, report)
        self.assertIn("Real evidence", markup)
        self.assertNotIn("No evidence", markup)
        self.assertNotIn("Sin pistas todavía", markup)
        self.assertIn("Archivo identificado en release", markup)

    def test_sheet_original_title_is_searchable_and_escaped(self):
        item = {
            "id": "alias", "title_id": "ALIAS0001", "name": "Internal Name",
            "repositories": [],
            "candidates": [{
                "kind": "community-sheet",
                "source": "PS Vita Ports Tracker (Reddit / Google Sheets)",
                "title": "Hoja comunitaria",
                "url": sheet.SHEET_URL + "&range=A12",
                "match_method": "long-description",
                "sheet_title": "The Original Game <script>alert(1)</script>",
            }],
        }
        markup = ui.add_data_tab(self.DOC, {"apps": [item]})
        self.assertIn("Nombre original en descripción larga", markup)
        self.assertIn("The Original Game", markup)
        self.assertNotIn("<script>alert(1)</script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_untrusted_title_is_escaped(self):
        html = ui.add_data_tab(self.DOC, {
            "apps": [{"id": "bad", "name": '<img src=x onerror=alert(1)>',
                      "title_id": "BAD123456", "reason": "<script>alert(1)</script>",
                      "repositories": [],
                      "candidates": [{"source": "GitHub", "title": "<svg/onload=alert(1)>",
                                      "url": "https://github.com/example/repo/releases"}]}]
        })
        self.assertNotIn("<svg/onload", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn('<img src=x onerror=alert(1)>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_rejects_non_project_urls(self):
        self.assertEqual(ui.safe_href("javascript:alert(1)"), "")
        self.assertEqual(ui.safe_href("https://evil.example/bad"), "")


if __name__ == "__main__":
    unittest.main()
