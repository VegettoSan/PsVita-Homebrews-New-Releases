#!/usr/bin/env python3
"""Offline verification of the existing release-card filters, labels and counts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import enrich_dashboard


def release(app_id, catalog, backup, repo="example/homebrew"):
    return {
        "platform": "github",
        "repo": repo,
        "tag": "v1.3",
        "name": "Homebrew",
        "published": "2026-11-16T11:00:00Z",
        "url": "https://github.com/example/homebrew/releases/tag/v1.3",
        "homebrew_id": app_id,
        "homebrew_name": app_id,
        "catalog_update_status": catalog,
        "catalog_update_detail": "Comparación de versiones",
        "catalog_version": "1.3",
        "backup_status": backup,
        "backup_detail": "Estado del enlace de VPK",
        "assets": [{"name": "Homebrew.vpk",
                    "size": 1234,
                    "download_url": "https://github.com/example/homebrew/releases/download/v1.3/Homebrew.vpk"}],
    }


class TestDashboardFilters(unittest.TestCase):
    def render(self):
        records = [
            release("catalog-current-no-backup", "uptodate", "missing"),
            release("catalog-old-backup-old", "pending", "outdated"),
            release("catalog-current-archived", "uptodate", "matched"),
            release("catalog-current-review", "uptodate", "review"),
            release("catalog-pending-unknown", "pending", "unknown"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.html"
            with patch.object(enrich_dashboard, "HTML_FILE", target):
                enrich_dashboard.generate_dashboard(records, {"synced_at": "2026-11-16T11:00:00Z"})
            return target.read_text(encoding="utf-8")

    def test_existing_and_archive_filter_controls_exist(self):
        html = self.render()
        for token in (
            'id="search"', 'id="platform"', 'id="catalog-status"',
            'id="backup-status"', 'id="clear-filters"',
            'id="no-results"', 'role="status"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, html)

    def test_statuses_and_combined_filter_conditions(self):
        html = self.render()
        self.assertIn('data-catalog="uptodate" data-backup="missing"', html)
        self.assertIn('data-catalog="pending" data-backup="outdated"', html)
        self.assertIn('data-catalog="uptodate" data-backup="matched"', html)
        self.assertIn("const show=textOk&&platformOk&&statusOk&&backupOk;", html)
        self.assertIn("card.dataset.backup==='missing'||card.dataset.backup==='outdated'", html)
        self.assertIn("backupStatus.addEventListener('change',filterCards);", html)
        self.assertIn("backupStatus.value='all';", html)
        self.assertIn('noResults.hidden=visible!==0 || cards.length===0;', html)

    def test_exact_counts_in_dropdowns(self):
        html = self.render()
        self.assertIn('Al día en PSVitaAlive (3)', html)
        self.assertIn('Pendientes por actualizar (2)', html)
        self.assertIn('Pendientes de respaldar (2)', html)
        self.assertIn('En Archive.org (1)', html)
        self.assertIn('Sin respaldo actual (1)', html)
        self.assertIn('Nueva versión sin respaldo (1)', html)
        self.assertIn('Revisar respaldo (1)', html)
        self.assertIn('Sin ficha asociada (1)', html)

    def test_preserves_downloads_and_version_labels_in_same_cards(self):
        html = self.render()
        self.assertIn('href="https://github.com/example/homebrew/releases/download/v1.3/Homebrew.vpk"', html)
        self.assertIn('Al día en PSVitaAlive', html)
        self.assertIn('Respaldo en Archive.org', html)
        self.assertIn('Falta respaldo en Archive.org', html)
        self.assertIn('Versión nueva sin respaldo', html)


if __name__ == "__main__":
    unittest.main()
