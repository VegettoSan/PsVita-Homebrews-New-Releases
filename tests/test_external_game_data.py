"""Offline regression tests for Game Data discovery from a public JSON snapshot."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import discover_game_data as discovery
import external_game_data as source
import game_data_dashboard as dashboard


def port():
    return {
        "id": "test-port", "name": "Test Port",
        "title_id": "PORT00001", "category_id": "ports",
        "links": [{"type": "Repository", "url": "https://github.com/dev/test-port"}],
    }


def snapshot_record(url="https://cdn.example.org/game-data.zip"):
    return {
        "type": "app", "category": "Port", "title_id": "PORT00001",
        "name": "Test Port", "download_url": "https://cdn.example.org/game.vpk",
        "data_url": url, "data_size": "1234",
        "packages": [
            {"kind": "game_files", "url": url, "size": 1234},
            {"kind": "plugin", "url": "https://cdn.example.org/plugin.zip"},
        ],
        "game_data_languages": [
            {"language": "es", "url": "https://cdn.example.org/data-es.zip", "size": 456},
        ],
    }


class PublicSnapshotTests(unittest.TestCase):
    def test_filters_files_and_deduplicates_base_and_languages(self):
        result = source.match_apps(discovery.missing_ports([port()]),
                                   [snapshot_record()])
        self.assertEqual(set(result), {"test-port"})
        self.assertEqual(len(result["test-port"]), 2)
        self.assertEqual(result["test-port"][0]["size"], 1234)
        self.assertEqual(result["test-port"][1]["language"], "es")
        self.assertNotIn("VitaForge", repr(result))

    def test_duplicate_title_ids_and_commercial_games_are_not_matched(self):
        records = [snapshot_record(),
                   snapshot_record("https://cdn.example.org/other.zip"),
                   {"type": "psv_game", "category": "Port",
                    "title_id": "PORT00001", "data_url": "https://example.org/pkg.zip"}]
        self.assertEqual(source.match_apps(discovery.missing_ports([port()]),
                                           records), {})

    def test_rejects_invalid_archive_urls(self):
        for url in (
            "https://github.com", "http://example.org/data.zip",
            "https://example.org/download.vpk", "https://127.0.0.1/data.zip",
            "https://example.org@evil.com/data.zip",
            "https://localhost/data.zip", "https://example.local/data.zip",
        ):
            with self.subTest(url=url):
                self.assertFalse(source.archive_url(url))
        self.assertTrue(source.archive_url(
            "https://archive.org/download/game/data.ZIP?download=1"))

    def test_downloads_bounded_live_snapshot(self):
        payload = {"data": [snapshot_record()]}
        class Reply:
            headers = {"Content-Length": "100"}
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def raise_for_status(self):
                pass
            def iter_content(self, **_):
                yield json.dumps(payload).encode("utf-8")
        with patch.object(source.requests, "get", return_value=Reply()) as getter:
            self.assertEqual(source.fetch_snapshot(), payload["data"])
        self.assertTrue(getter.call_args.kwargs.get("stream"))

    def test_rejects_invalid_snapshot_shape(self):
        class Reply:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def raise_for_status(self): pass
            def iter_content(self, **_): yield b'{"not_data":[]}'
        with patch.object(source.requests, "get", return_value=Reply()):
            with self.assertRaises(ValueError):
                source.fetch_snapshot()

    def test_report_adds_external_lead_without_overwriting_existing_sources(self):
        catalog = [port()]
        sheet = {
            "kind": "community-sheet", "source": "PS Vita Ports Tracker (test)",
            "url": discovery.SHEET_URL + "&range=A8", "title": "Hoja"
        }
        first, _ = discovery.build_report(
            catalog, {}, scanner=lambda _: ([], []),
            external_rows=[snapshot_record()],
            sheet_rows=[], timestamp="2026-11-16T00:00:00Z")
        result = first["apps"][0]["candidates"]
        self.assertEqual([c["kind"] for c in result],
                         [source.KIND, source.KIND])
        self.assertTrue(all(discovery.is_strong_candidate(c) for c in result))
        self.assertEqual(first["new_matches"], 2)

    def test_failure_preserves_previous_snapshot_references(self):
        catalog = [port()]
        first, _ = discovery.build_report(
            catalog, {}, scanner=lambda _: ([], []),
            external_rows=[snapshot_record()], sheet_rows=[])
        failed, _ = discovery.build_report(
            catalog, first, scanner=lambda _: ([], []),
            external_rows=None, external_error="ConnectionError", sheet_rows=[])
        self.assertEqual(failed["external_status"], "error")
        self.assertEqual(failed["apps"][0]["candidates"],
                         first["apps"][0]["candidates"])
        empty, _ = discovery.build_report(
            catalog, failed, scanner=lambda _: ([], []),
            external_rows=[], sheet_rows=[])
        self.assertEqual(empty["possible_matches"], 0)

    def test_web_uses_neutral_labels_with_safe_archive_link(self):
        report, _ = discovery.build_report(
            [port()], {}, scanner=lambda _: ([], []),
            external_rows=[snapshot_record()], sheet_rows=[])
        page = dashboard.render_panel(report)
        self.assertIn('value="catalog"', page)
        self.assertIn("Enlace de archivo sin verificar", page)
        self.assertIn('href="https://cdn.example.org/game-data.zip"', page)
        self.assertIn("Idioma: es", page)
        self.assertNotIn("VitaForge", page)


if __name__ == "__main__":
    unittest.main()
