import unittest
from copy import deepcopy
from discovery_readiness import readiness, REQUIRED


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.entry = {"app_draft": {"id": "demo", "name": "Demo", "description": "An original demo.",
            "author_ids": ["dev"], "version": "1.0", "version_date": "2026-01-01",
            "size": 12345, "status": "Legacy", "icon": "https://example.org/icon.png",
            "screenshots": [], "links": [
                {"type": "Repository", "name": "Source", "url": "https://github.com/dev/demo"},
                {"type": "Download", "name": "VPK", "url": "https://example.org/demo.vpk"}]}}
        self.result = {"title": {"state": "high", "suggestion": "DEMO00001"},
            "category": {"state": "suggested", "category_id": "demos", "subcategory_ids": []},
            "requirements": {"state": "no_data", "value": "- no data"}}
        self.categories = [{"id": "demos", "subcategories": [{"id": "other"}]}]
        self.authors = [{"id": "dev"}]
        self.registry = {"apps": {"ids": [], "title_ids": []}, "authors": {"ids": []}}
        self.apps = []

    def report(self):
        return readiness(self.entry, self.result, self.categories, self.authors, self.apps, self.registry)

    def test_complete_metadata_with_safe_defaults_not_publication(self):
        before = deepcopy(self.entry)
        r = self.report()
        self.assertEqual(r["required_present"], len(REQUIRED))
        self.assertTrue(r["metadata_complete"])
        self.assertFalse(r["publication_allowed"])
        self.assertEqual(r["proposed_defaults"], {"requirements": "- no data",
            "long_description": "An original demo.", "subcategory_ids": ["other"]})
        self.assertEqual(self.entry, before)

    def test_icon_required_and_screenshots_not_required(self):
        self.entry["app_draft"]["icon"] = ""
        self.assertEqual(self.report()["missing_required"], ["icon"])
        self.entry["app_draft"].pop("screenshots")
        self.assertEqual(self.report()["missing_required"], ["icon"])

    def test_subcategory_cannot_be_invented(self):
        self.categories[0]["subcategories"] = [{"id": "tech-demo"}]
        self.assertIn("subcategory_ids", self.report()["missing_required"])

    def test_conflicts_and_weak_title(self):
        self.apps = [{"id": "demo", "title_id": "DEMO00001"}]
        self.assertEqual(self.report()["missing_required"][:2], ["id", "title_id"])
        self.apps = []
        self.result["title"]["state"] = "medium"
        self.assertIn("title_id", self.report()["missing_required"])

    def test_retired_and_unapproved_authors(self):
        self.registry["apps"]["ids"].append("demo")
        self.registry["authors"]["ids"].append("dev")
        self.assertEqual(self.report()["missing_required"], ["id", "author_ids"])

    def test_no_fabricated_version_status_size_or_author(self):
        self.entry["app_draft"].update(version="", version_date="", size=0, status="", author_ids=[])
        self.assertEqual(self.report()["missing_required"], ["author_ids", "version", "version_date", "size", "status"])

    def test_conflicting_requirements_require_review(self):
        self.result["requirements"]["state"] = "review"
        self.assertEqual(self.report()["missing_required"], ["requirements"])

    def test_invalid_links_and_date(self):
        self.entry["app_draft"]["version_date"] = "2026-02-30"
        self.entry["app_draft"]["links"][1]["url"] = "file:///tmp/app.vpk"
        self.assertEqual(self.report()["missing_required"], ["version_date", "links"])


if __name__ == "__main__":
    unittest.main()
