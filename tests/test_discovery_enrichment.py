import struct
import unittest
from unittest.mock import patch

from discovery_enrichment import (
    classify_category, prioritized_paths, resolve_title, sfo_title_id, text_evidence,
)
from catalog_schema_v2_compat import to_v1
import preview_discovery_enrichment as pilot


class IdentityTests(unittest.TestCase):
    def test_nested_sources_priority(self):
        self.assertEqual(prioritized_paths(["src/a.cpp", "deep/vita/CMakeLists.txt",
            "deep/assets/sce_sys/param.sfo", "vendor/param.sfo", "../param.sfo"]),
            ["deep/assets/sce_sys/param.sfo", "deep/vita/CMakeLists.txt", "src/a.cpp"])

    def test_declarations(self):
        for text in ['set(VITA_TITLEID "EXAM00001")', 'TITLE_ID := EXAM00001',
                     '#define TITLE_ID "EXAM00001"', '"title_id": "EXAM00001"',
                     "| Title ID | EXAM00001 |", "<TITLE_ID>EXAM00001</TITLE_ID>",
                     "psp2TitleId: EXAM00001", "-s TITLE_ID=EXAM00001"]:
            with self.subTest(text=text):
                self.assertEqual(resolve_title(text_evidence(text, "source", "build"))["suggestion"], "EXAM00001")

    def test_bare_token_and_comments(self):
        self.assertFalse(text_evidence("EXAM00001\n// TITLE_ID=EXAM00001\nTITLE_ID=EXAM000011", "a.cpp", "code"))

    def test_multiple_ids_not_first_match(self):
        ev = text_evidence('set(VITA_TITLEID "PSVAS1178")\nset(UPDATER_TITLEID "PSVAUPDT1")', "CMakeLists.txt", "build")
        self.assertEqual(resolve_title(ev)["state"], "ambiguous")
        self.assertEqual(resolve_title(ev)["suggestion"], "")

    def test_retired_and_existing(self):
        ev = text_evidence("TITLE_ID=EXAM00001", "Makefile", "build")
        self.assertEqual(resolve_title(ev, ["exam00001"])["state"], "collision")
        self.assertEqual(resolve_title(ev, [], ["EXAM00001"])["state"], "retired")

    def test_readme_is_not_package_verification(self):
        ev = text_evidence("Title ID: EXAM00001", "README.md", "documentation")
        self.assertEqual(resolve_title(ev)["state"], "medium")

    def test_sfo(self):
        raw = (struct.pack("<4sIIII", b"\0PSF", 0x101, 36, 48, 1)
               + struct.pack("<HHIII", 0, 0x0204, 10, 12, 0)
               + b"TITLE_ID\0\0\0\0" + b"EXAM00001\0\0\0")
        self.assertEqual(sfo_title_id(raw), "EXAM00001")
        self.assertEqual(sfo_title_id(raw[:-1]), "")
        self.assertEqual(sfo_title_id(b"garbage"), "")


class TaxonomyTests(unittest.TestCase):
    taxonomy = to_v1([
        {"id": "ports", "subs": [{"id": "fps"}]},
        {"id": "games", "subs": [{"id": "puzzle"}]},
        {"id": "emulators", "subs": [{"id": "game-boy-advance"}]},
        {"id": "plugins", "subs": [{"id": "overclock"}, {"id": "performance"}]},
        {"id": "utilities", "subs": [{"id": "file-manager"}]},
        {"id": "networking", "subs": [{"id": "client"}]},
        {"id": "modding", "subs": [{"id": "translation"}]},
    ])

    def classify(self, desc, kind="homebrew"):
        return classify_category("", desc, "", kind, self.taxonomy)

    def test_game_port(self):
        res = self.classify("Game port of a first-person shooter")
        self.assertEqual(res["category_id"], "ports")
        self.assertEqual(res["subcategory_ids"], ["fps"])

    def test_emulator_port(self):
        self.assertEqual(self.classify("Port of a Game Boy Advance emulator")["category_id"], "emulators")

    def test_utility_port(self):
        self.assertEqual(self.classify("Port of a file manager")["category_id"], "utilities")

    def test_plugin(self):
        self.assertEqual(self.classify("Overclock tool", "plugin")["subcategory_ids"], ["overclock", "performance"])

    def test_original_game(self):
        self.assertEqual(self.classify("Original puzzle game")["category_id"], "games")

    def test_unknown_and_ambiguous(self):
        self.assertEqual(self.classify("PS Vita project")["state"], "missing")
        self.assertEqual(self.classify("File manager and SSH client")["state"], "ambiguous")

    def test_taxonomy_only(self):
        result = classify_category("", "Game port of a first-person shooter", "", "homebrew",
                                   [{"id": "ports", "subcategories": []}])
        self.assertEqual(result["subcategory_ids"], [])


class PreviewTests(unittest.TestCase):
    def test_invalid_project_paths(self):
        for p in ["../repo", "org/repo?x=1", "https://github.com/org/repo"]:
            with self.assertRaises(ValueError):
                pilot.safe_project(p, "github")

    def test_read_file_size_and_encoding(self):
        with patch.object(pilot.discovery, "request", return_value={"size": 3, "encoding": "base64", "content": "YWJj"}):
            self.assertEqual(pilot.read_file("https://api.github.com/repos/a/b", "github", "nested/file", "a"*40), b"abc")
        with patch.object(pilot.discovery, "request", return_value={"size": 9999999, "encoding": "base64", "content": "YWJj"}):
            self.assertIsNone(pilot.read_file("https://api.github.com/repos/a/b", "github", "nested/file", "a"*40))

    def test_preview_never_authorizes_publication_and_screens_optional(self):
        entry = {"key": "gh:1", "project": "a/b", "platform": "github", "app_draft": {"icon": "x", "screenshots": []}}
        with patch.object(pilot, "provider_repo", return_value=({}, "https://api.github.com/repos/a/b", "a"*40)), \
             patch.object(pilot.discovery, "files_for", return_value=["CMakeLists.txt"]), \
             patch.object(pilot.discovery, "request", return_value=[]), \
             patch.object(pilot, "read_file", return_value=b'TITLE_ID=EXAM00001'):
            row = pilot.inspect(entry, TaxonomyTests.taxonomy, [], [], 5)
        self.assertEqual(row["title"]["suggestion"], "EXAM00001")
        self.assertTrue(row["icon_present"])
        self.assertEqual(row["screenshots_count"], 0)
        self.assertFalse(row["publication_allowed"])


if __name__ == "__main__":
    unittest.main()
