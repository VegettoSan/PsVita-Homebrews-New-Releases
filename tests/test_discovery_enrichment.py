import struct
import unittest
from unittest.mock import patch

from discovery_enrichment import (
    classify_category, prioritized_paths, resolve_title, sfo_title_id, text_evidence,
)
from catalog_schema_v2_compat import to_v1
import preview_discovery_enrichment as pilot
from discovery_requirements import extract_requirements, resolve_requirements, relevant_document


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
        {"id": "media", "subs": [{"id": "audio"}, {"id": "music-player"}]},
        {"id": "customization", "subs": [{"id": "themes"}]},
        {"id": "demos", "subs": [{"id": "proof-of-concept"}, {"id": "tech-demo"}]},
        {"id": "system", "subs": [{"id": "firmware"}]},
        {"id": "development", "subs": [{"id": "interpreter"}, {"id": "lua"}]},
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


    def test_tracplayer_purpose_over_secondary_features(self):
        readme = "# TRAC Player\n\n## The Retro Audio Collection Player\n\nBrowse a music archive.\n\n------\n- Custom theme support\n- Download tracks\n## Theme Support\nCreate custom themes."
        result = classify_category("TRACPlayer", "", readme, "homebrew", self.taxonomy)
        self.assertEqual(result["category_id"], "media")
        self.assertIn("audio", result["subcategory_ids"])
        self.assertNotIn("customization", result["scores"])

    def test_desktop_port_is_review_not_vita_game(self):
        readme = "# Lua Player Plus for Desktop\n\nA cross-platform port of the PS Vita homebrew interpreter to desktop platforms using SDL2.\n## Features\n- launcher"
        result = classify_category("lpp-sdl", "", readme, "homebrew", self.taxonomy)
        self.assertEqual(result["state"], "target_review")
        self.assertEqual(result["category_id"], "")

    def test_qr_demo_and_firmware(self):
        result = classify_category("qrdemo", "", "# QR Demo for Vita\nDecode QR codes from the camera.\nThis is a PoC.", "homebrew", self.taxonomy)
        self.assertEqual(result["category_id"], "demos")
        self.assertEqual(self.classify("Custom Firmware for PSP and PS Vita ePSP")["category_id"], "system")

    def test_feature_list_alone_does_not_classify(self):
        result = classify_category("Unknown app", "", "# Unknown app\n## Features\n- custom theme support\n- file manager", "homebrew", self.taxonomy)
        self.assertEqual(result["state"], "missing")

    def test_root_readme_not_starved_by_nested_config_files(self):
        paths = [f"lib/{i}/CMakeLists.txt" for i in range(30)] + ["README.md", "sce_sys/param.sfo"]
        self.assertIn("README.md", prioritized_paths(paths)[:3])


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


    def test_individual_failure_does_not_stop_batch(self):
        rows = [(key, {"key": key, "project": key}) for key in ("a", "b", "c")]
        with patch.object(pilot, "inspect", side_effect=[
                ValueError("bad candidate"), {"key": "b"}, {"key": "c"}]) as scan:
            report = pilot.inspect_batch(rows, [], [], [], 5)
        self.assertEqual(scan.call_count, 3)
        self.assertEqual(report["results"][0]["state"], "candidate_error")
        self.assertTrue(report["results"][0]["retry"])
        self.assertEqual(report["results"][2]["key"], "c")
        self.assertFalse(report["budget_stopped"])

    def test_malformed_candidate_and_unexpected_error_are_isolated(self):
        rows = [("a", None), ("b", {"key": "b"}), ("c", {"key": "c"})]
        with patch.object(pilot, "inspect", side_effect=[AttributeError("bug"), {"key": "c"}]):
            report = pilot.inspect_batch(rows, [], [], [], 5)
        self.assertEqual([r.get("state") for r in report["results"][:2]],
                         ["candidate_error", "internal_error"])
        self.assertEqual(report["results"][2]["key"], "c")
        self.assertEqual(report["internal_errors"], 1)
        self.assertFalse(report["budget_stopped"])

    def test_global_budget_defers_remaining_candidates(self):
        rows = [(key, {"key": key}) for key in ("a", "b", "c")]
        with patch.object(pilot, "inspect", side_effect=[
                {"key": "a"}, pilot.discovery.DiscoveryBudgetReached("budget")]) as scan:
            report = pilot.inspect_batch(rows, [], [], [], 5)
        self.assertEqual(scan.call_count, 2)
        self.assertTrue(report["budget_stopped"])
        self.assertEqual(report["results"][1]["state"], "deferred")
        self.assertEqual(report["unattempted_keys"], ["c"])

    def test_file_failure_preserves_later_evidence(self):
        entry = {"key": "gh:1", "project": "a/b", "platform": "github"}
        with patch.object(pilot, "provider_repo", return_value=({}, "https://api.github.com/repos/a/b", "a"*40)), \
             patch.object(pilot.discovery, "files_for", return_value=["a/Makefile", "b/Makefile"]), \
             patch.object(pilot.discovery, "request", side_effect=pilot.requests.Timeout()), \
             patch.object(pilot, "read_file", side_effect=[pilot.requests.Timeout(), b'TITLE_ID=EXAM00001']):
            row = pilot.inspect(entry, TaxonomyTests.taxonomy, [], [], 5)
        self.assertEqual(row["title"]["suggestion"], "EXAM00001")
        self.assertEqual(row["state"], "partial")
        self.assertTrue(row["retry"])
        self.assertEqual(len(row["errors"]), 2)
        self.assertFalse(row["publication_allowed"])



class RequirementsTests(unittest.TestCase):
    def result(self, text):
        return resolve_requirements(extract_requirements(text, "README.md", "a"*40))

    def test_missing_uses_exact_placeholder(self):
        self.assertEqual(self.result("# Demo\nThis is a demo.")["value"], "- no data")
        self.assertEqual(self.result("## Building\n- Install VitaSDK\n- Requires CMake and SDL2")["value"], "- no data")

    def test_runtime_requirements_and_installation(self):
        result = self.result("## Requirements\n- kuBridge plugin\n- Original game files\n## Installation\n1. Copy data files to ux0:data/demo/")
        self.assertEqual(result["state"], "detected")
        self.assertEqual(result["value"], "- kuBridge plugin\n- Original game files\n- Copy data files to ux0:data/demo/")
        self.assertEqual(result["evidence"][0]["line"], 2)

    def test_explicit_none_is_not_unknown(self):
        result = self.result("## Requirements\nNone")
        self.assertEqual(result["state"], "explicit_none")
        self.assertEqual(result["value"], "- None")

    def test_optional_and_build_dependencies_are_not_required(self):
        result = self.result("## Requirements\n- Optional plugin\n## Building\n- Install SDL2\n- Copy game data for build testing")
        self.assertEqual(result["value"], "- no data")

    def test_duplicate_and_conflicting_requirements(self):
        result = self.result("## Requirements\nNone\n- kuBridge plugin\n- kuBridge plugin")
        self.assertEqual(result["state"], "review")
        self.assertEqual(result["value"], "- kuBridge plugin")

    def test_document_scope(self):
        self.assertTrue(relevant_document("docs/INSTALL.md"))
        self.assertFalse(relevant_document("requirements.txt"))
        self.assertFalse(relevant_document("vendor/library/README.md"))


if __name__ == "__main__":
    unittest.main()
