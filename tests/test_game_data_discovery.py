"""Offline regression tests for the independent Game/Data Files discovery."""
import unittest
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
                             "source": "GitHub Releases", "title": "Old"}]}]}
        changed = [{
            "id": "port-one", "name": "Port One", "category_id": "ports",
            "links": [{"type": "Repository", "url": "https://github.com/dev/game"}]
        }]
        def fake_scanner(_):
            return ([{"url": "https://github.com/dev/game/releases/tag/new",
                      "source": "GitHub Releases", "title": "New"}], [])
        report, new = gd.build_report(changed, previous, scanner=fake_scanner,
                                      timestamp="2026-11-16T12:00:00Z")
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["new_matches"], 1)
        self.assertEqual(len(new), 1)
        changed[0]["links"].append({"type": "Game Files", "url": "https://example.org/files.zip"})
        report, new = gd.build_report(changed, report, scanner=fake_scanner)
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(new, [])

    def test_scan_failure_preserves_previous_evidence(self):
        app = port("port-one", links=[
            {"type": "Repository", "url": "https://github.com/dev/game"}
        ])
        prior = {"apps": [{"id": "port-one", "checked_at": "2026-11-15",
                           "candidates": [{"url": "https://github.com/dev/game/releases/tag/a"}]}]}
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
        self.assertIn('id="releases">Release content', html)
        self.assertIn("location.hash === '#game-data'", html)

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
