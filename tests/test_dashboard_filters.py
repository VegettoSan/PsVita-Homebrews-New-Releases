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

    def test_sonic_r_transfer_reassociates_by_exact_github_release_identity(self):
        old = release("missing-sonic-r", "pending", "unknown", repo="fr3dsp/sonic-r-vita")
        old.update({
            "tag": "v2.1",
            "release_id": "448956819",
            "published": "2026-11-17T17:15:58Z",
            "url": "https://github.com/PupueStudios/sonic-r-vita/releases/tag/v2.1",
            "assets": [{
                "name": "sonicr_vita.vpk", "size": 4363896,
                "download_url": "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/sonicr_vita.vpk",
            }],
        })
        canonical = {
            "platform": "github", "repo": "PupueStudios/sonic-r-vita",
            "tag": "v2.1", "release_id": "448956819",
            "published": "2026-11-17T17:15:58Z",
            "assets": old["assets"].copy(),
        }
        registered = {
            "id": "sonic-r", "name": "Sonic R",
            "version": "2.1", "icon": "https://archive.org/sonic-r-icon.png",
            "category": "ports", "author_ids": ["fr3dsp"],
            "links": [{
                "type": "Download", "name": "VPK",
                "url": "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/sonicr_vita.vpk",
            }, {
                "type": "Repository", "name": "GitHub",
                "url": "https://github.com/PupueStudios/sonic-r-vita",
            }],
        }
        index = enrich_dashboard.catalog_repo_index([registered])
        self.assertNotIn("github:fr3dsp/sonic-r-vita", index)
        self.assertEqual(len(index["github:pupuestudios/sonic-r-vita"]), 1)
        history = [old]
        repaired = enrich_dashboard.reconcile_renamed_github_releases(
            history, {"schema_version": 1, "releases": [canonical]}, index
        )
        self.assertEqual(repaired, 1)
        self.assertEqual(old["previous_repo"], "fr3dsp/sonic-r-vita")
        self.assertEqual(old["repo"], "PupueStudios/sonic-r-vita")
        enrich_dashboard.enrich_release(old, index["github:pupuestudios/sonic-r-vita"])
        self.assertEqual(old["homebrew_name"], "Sonic R")
        self.assertEqual(old["homebrew_icon"], "https://archive.org/sonic-r-icon.png")
        self.assertEqual(old["homebrew_id"], "sonic-r")
        self.assertEqual(old["catalog_update_status"], "uptodate")
        self.assertEqual(old["catalog_version"], "2.1")
        self.assertEqual(
            enrich_dashboard.reconcile_renamed_github_releases(
                history, {"schema_version": 1, "releases": [canonical]}, index
            ), 0
        )

    def test_rename_matching_rejects_unverified_and_ambiguous_identity(self):
        original = release("sonic", "pending", "unknown", "fr3dsp/sonic-r-vita")
        original.update({
            "release_id": "448956819", "tag": "v2.1",
            "published": "2026-11-17T17:15:58Z",
            "url": "https://github.com/PupueStudios/sonic-r-vita/releases/tag/v2.1",
            "assets": [{"name": "sonicr_vita.vpk",
                "download_url": "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/sonicr_vita.vpk"}],
        })
        current = {
            "platform": "github", "repo": "PupueStudios/sonic-r-vita",
            "release_id": "448956819", "tag": "v2.1",
            "published": "2026-11-17T17:15:58Z",
            "assets": original["assets"].copy(),
        }
        index = {
            "github:pupuestudios/sonic-r-vita": [{"id": "sonic-r", "name": "Sonic R"}]
        }
        changed = [
            {**current, "release_id": "999999"},
            {**current, "tag": "v2.2"},
            {**current, "published": "2026-11-19T00:00:00Z"},
            {**current, "assets": [{
                "name": "evil.vpk",
                "download_url": "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/evil.vpk"}]},
        ]
        for wrong in changed:
            with self.subTest(wrong=wrong):
                candidate = dict(original)
                self.assertEqual(
                    enrich_dashboard.reconcile_renamed_github_releases(
                        [candidate], {"schema_version": 1, "releases": [wrong]}, index
                    ), 0
                )
                self.assertEqual(candidate["repo"], "fr3dsp/sonic-r-vita")
        for bad_url in [
            "https://github.com/fr3dsp/sonic-r-vita/releases/tag/v2.1",
            "https://github.com/PupueStudios/sonic-r-vita/releases/tag/other",
            "https://evil.example/PupueStudios/sonic-r-vita/releases/tag/v2.1",
        ]:
            candidate = {**original, "url": bad_url}
            self.assertEqual(
                enrich_dashboard.reconcile_renamed_github_releases(
                    [candidate], {"schema_version": 1, "releases": [current]}, index
                ), 0
            )
        ambiguous = {
            "github:pupuestudios/sonic-r-vita": [
                {"id": "sonic-r"}, {"id": "another-port"}
            ]
        }
        self.assertEqual(
            enrich_dashboard.reconcile_renamed_github_releases(
                [dict(original)], {"schema_version": 1, "releases": [current]}, ambiguous
            ), 0
        )
        self.assertEqual(
            enrich_dashboard.reconcile_renamed_github_releases(
                [dict(original)], {"schema_version": 1, "releases": [current, current]}, index
            ), 0
        )

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

    def test_pending_without_a_unique_vpk_explains_manual_review(self):
        no_vpk = release("no-vpk", "pending", "missing")
        no_vpk["assets"] = [{"name": "bundle.zip", "size": 1234, "download_url": "https://github.com/example/homebrew/releases/download/v1.3/bundle.zip"}]
        many_vpks = release("multi-vpk", "pending", "missing")
        many_vpks["assets"] = [
            {"name": "a.vpk", "download_url": "https://github.com/example/homebrew/releases/download/v1.3/a.vpk"},
            {"name": "b.vpk", "download_url": "https://github.com/example/homebrew/releases/download/v1.3/b.vpk"},
        ]
        # The dashboard preserves the existing Pending status, but gives a reason.
        # The updater must then leave these catalog JSONs untouched.
        for source, expected in (
            (no_vpk, "Revisión manual: release sin VPK"),
            (many_vpks, "Revisión manual: 2 VPK diferentes"),
        ):
            with self.subTest(expected=expected):
                with patch.object(enrich_dashboard, "calculate_catalog_status",
                                  return_value=("pending", "Version nueva")):
                    with patch.object(enrich_dashboard, "choose_primary_app",
                                      return_value={"id": "no-vpk", "name": "Test", "version": "1.2"}):
                        enrich_dashboard.enrich_release(source, [{"id": "no-vpk", "name": "Test", "version": "1.2"}])
                self.assertEqual(source["catalog_update_status"], "pending")
                self.assertIn(expected, source["catalog_update_detail"])

    def test_preserves_downloads_and_version_labels_in_same_cards(self):
        html = self.render()
        self.assertIn('href="https://github.com/example/homebrew/releases/download/v1.3/Homebrew.vpk"', html)
        self.assertIn('Al día en PSVitaAlive', html)
        self.assertIn('Respaldo en Archive.org', html)
        self.assertIn('Falta respaldo en Archive.org', html)
        self.assertIn('Versión nueva sin respaldo', html)


if __name__ == "__main__":
    unittest.main()
