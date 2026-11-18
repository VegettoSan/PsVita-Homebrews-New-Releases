#!/usr/bin/env python3
"""The monitor keeps prereleases, stable fallback, and safe version ordering."""
import unittest
from unittest.mock import patch

import monitor
from enrich_dashboard import compare_versions


def entry(tag, prerelease, release_id):
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "published_at": "2026-11-16T10:00:00Z",
        "prerelease": prerelease,
        "assets": [{
            "name": "port.vpk",
            "size": 100,
            "browser_download_url": "https://github.com/dev/port/releases/download/" + tag + "/port.vpk",
        }],
    }


class PrereleaseMonitorTests(unittest.TestCase):
    def test_prerelease_kept_and_stable_fallback_exposed(self):
        releases = [
            entry("v1.2.0-beta.2", True, 12),
            entry("v1.1.0", False, 11),
        ]
        with patch.object(monitor, "request_json", return_value=releases) as request:
            result = monitor.get_github_release("dev/port")
        self.assertEqual(request.call_args.kwargs["params"], {"per_page": 20})
        self.assertEqual(result["tag"], "v1.2.0-beta.2")
        self.assertTrue(result["prerelease"])
        self.assertEqual(result["assets"][0]["name"], "port.vpk")
        stable = result["stable_release"]
        self.assertFalse(stable["prerelease"])
        self.assertEqual(stable["tag"], "v1.1.0")
        self.assertEqual(stable["assets"][0]["name"], "port.vpk")

    def test_prerelease_only_has_no_false_stable_fallback(self):
        with patch.object(monitor, "request_json", return_value=[
            entry("v1.2.0-rc1", True, 12), entry("v1.2.0-beta2", True, 11),
        ]):
            result = monitor.get_github_release("dev/port")
        self.assertTrue(result["prerelease"])
        self.assertNotIn("stable_release", result)

    def test_drafts_ignored(self):
        draft = entry("v3.0", False, 13)
        draft["draft"] = True
        with patch.object(monitor, "request_json", return_value=[
            draft, entry("v1.2.0-beta2", True, 12),
        ]):
            result = monitor.get_github_release("dev/port")
        self.assertEqual(result["tag"], "v1.2.0-beta2")

    def test_verified_renamed_repo_includes_canonical_provenance(self):
        latest = entry("v2.1", False, 448956819)
        latest["html_url"] = "https://github.com/PupueStudios/sonic-r-vita/releases/tag/v2.1"
        latest["assets"][0]["browser_download_url"] = (
            "https://github.com/PupueStudios/sonic-r-vita/releases/download/v2.1/sonicr_vita.vpk"
        )
        def response(url, **kwargs):
            if url.endswith("/releases"):
                return [latest]
            if url.endswith("/repos/fr3dsp/sonic-r-vita"):
                return {"full_name": "PupueStudios/sonic-r-vita", "id": 1378201322}
            raise AssertionError("Unexpected URL: " + url)
        with patch.object(monitor, "request_json", side_effect=response):
            result = monitor.get_github_release("fr3dsp/sonic-r-vita")
        self.assertEqual(result["repo"], "fr3dsp/sonic-r-vita")
        self.assertEqual(result["canonical_repo"], "PupueStudios/sonic-r-vita")
        self.assertEqual(result["repo_id"], 1378201322)

    def test_unverified_repo_move_never_adds_canonical_provenance(self):
        latest = entry("v2.1", False, 448956819)
        latest["html_url"] = "https://github.com/unrelated/other/releases/tag/v2.1"
        with patch.object(monitor, "request_json",
                          side_effect=[[latest], {"full_name": "unrelated/different", "id": 123}]):
            result = monitor.get_github_release("fr3dsp/sonic-r-vita")
        self.assertNotIn("canonical_repo", result)
        self.assertNotIn("repo_id", result)

    def test_github_confirms_no_stable_release_after_twenty_prereleases(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        urls = []
        def response(url, **kwargs):
            urls.append(url)
            if url.endswith("/releases"):
                return releases
            if url.endswith("/releases/latest"):
                return None  # only an HTTP 404, never an API error, yields None
            raise AssertionError(url)
        with patch.object(monitor, "request_json", side_effect=response):
            record = monitor.get_github_release("dev/port")
        self.assertTrue(record["no_stable_release"])
        self.assertIn("/releases/latest", urls[-1])

    def test_older_stable_is_checked_even_after_twenty_prereleases(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        stable = entry("v0.9", False, 99)
        with patch.object(monitor, "request_json",
                          side_effect=[releases, stable]):
            record = monitor.get_github_release("dev/port")
        self.assertNotIn("no_stable_release", record)
        self.assertEqual(record["stable_release"]["tag"], "v0.9")

    def test_stable_lookup_error_does_not_claim_stable_absence(self):
        releases = [entry("v1.0-beta" + str(i), True, i)
                    for i in range(20, 0, -1)]
        with patch.object(monitor, "request_json",
                          side_effect=[releases, RuntimeError("API rate limit")]):
            record = monitor.get_github_release("dev/port")
        self.assertNotIn("no_stable_release", record)
        self.assertNotIn("stable_release", record)

    def test_golden_balloon_rolling_build_is_numbered_but_requires_review(self):
        draft = entry("vita-next", False, 449376784)
        draft["name"] = "Golden Balloon Vita - Next Build (v1.7.3)"
        draft["published_at"] = "2026-11-18T03:49:45Z"
        draft["body"] = (
            "<!-- vita-next-build-draft -->\n"
            "**Draft build: review these notes and validate on hardware before publishing.**\n"
            "App version " + chr(96) + "1.7.3" + chr(96) + " built from main."
        )
        vpk = "GoldenBalloon-Vita-v1.7.3-44079be4-44209555230-1.vpk"
        draft["assets"] = [
            {"name": vpk, "size": 4799043,
             "browser_download_url": "https://github.com/zm2283145/goldenballoon-vita-port/releases/download/vita-next/" + vpk},
            {"name": vpk + ".sha256", "size": 119,
             "browser_download_url": "https://github.com/zm2283145/goldenballoon-vita-port/releases/download/vita-next/" + vpk + ".sha256"},
        ]
        stable = entry("v1.7.2", False, 391110485)
        stable["published_at"] = "2026-09-17T23:04:32Z"
        with patch.object(monitor, "request_json", return_value=[draft, stable]):
            result = monitor.get_github_release("zm2283145/goldenballoon-vita-port")
        self.assertEqual(result["tag"], "vita-next")
        self.assertEqual(result["version"], "1.7.3")
        self.assertEqual(result["release_channel"], "rolling")
        self.assertTrue(result["review_required"])
        self.assertTrue(result["prerelease"])
        self.assertEqual(result["stable_release"]["tag"], "v1.7.2")
        from enrich_dashboard import calculate_catalog_status
        status, detail = calculate_catalog_status(result, {"version": "1.7.2"})
        self.assertEqual(status, "pending")
        self.assertIn("validación", detail)
        self.assertIn("1.7.3", detail)

    def test_rolling_requires_matching_title_vpk_and_body_version(self):
        build = entry("vita-next", False, 1)
        build["name"] = "Golden Balloon Vita - Next Build (v1.7.3)"
        build["body"] = "App version " + chr(96) + "1.7.3" + chr(96) + " built from main"
        build["assets"] = [{
            "name": "GoldenBalloon-Vita-v1.7.3-deadbeef.vpk", "size": 100,
            "browser_download_url": "https://github.com/dev/port/releases/download/vita-next/GoldenBalloon-Vita-v1.7.3-deadbeef.vpk",
        }]
        result = monitor.rolling_release_metadata(build)
        self.assertEqual(result["version"], "1.7.3")
        self.assertFalse(result["review_required"])
        for invalid in [
            {**build, "name": "Build v1.7.3 without proper title"},
            {**build, "body": "App version " + chr(96) + "1.7.4" + chr(96) + " built from main"},
            {**build, "assets": [{**build["assets"][0],
                                 "name": "GoldenBalloon-Vita-v1.7.2-deadbeef.vpk"}]},
            {**build, "assets": build["assets"] * 2},
        ]:
            result = monitor.rolling_release_metadata(invalid)
            self.assertNotIn("version", result, invalid)
        unrecognized = dict(build)
        unrecognized["tag_name"] = "unversioned-random"
        self.assertEqual(monitor.rolling_release_metadata(unrecognized), {})

    def test_moving_channel_updates_its_existing_history_card_without_announcing_drafts(self):
        import json
        import tempfile
        from pathlib import Path

        old = {
            "platform": "github", "repo": "dev/port", "tag": "vita-next",
            "name": "Next Build (v1.7.2)", "release_id": "1234",
            "published": "2026-11-10T10:00:00Z", "prerelease": True,
            "release_channel": "rolling", "version": "1.7.2",
            "review_required": True,
            "assets": [{"name": "port-v1.7.2.vpk", "download_url":
                        "https://github.com/dev/port/releases/download/vita-next/port-v1.7.2.vpk"}],
        }
        newer = {
            **old, "name": "Next Build (v1.7.3)", "version": "1.7.3",
            "assets": [{"name": "port-v1.7.3.vpk", "download_url":
                        "https://github.com/dev/port/releases/download/vita-next/port-v1.7.3.vpk"}],
        }
        project = {
            "platform": "github", "project": "dev/port",
            "key": "github:dev/port", "url": "https://github.com/dev/port",
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state_file = base / "state.json"
            history_file = base / "releases.json"
            snap_file = base / "upstream_latest.json"
            sync_file = base / "sync.json"
            state_file.write_text(json.dumps({"github:dev/port": {
                "tag": "vita-next", "release_id": "1234",
                "version": "1.7.2", "review_required": True,
                "vpk_url": old["assets"][0]["download_url"],
            }}), encoding="utf-8")
            history_file.write_text(json.dumps([old]), encoding="utf-8")
            with (
                patch.dict(monitor.os.environ, {"PSVA_MONITOR_CATALOG_REFRESH": "0"}),
                patch.object(monitor, "STATE_FILE", state_file),
                patch.object(monitor, "HISTORY_FILE", history_file),
                patch.object(monitor, "UPSTREAM_LATEST_FILE", snap_file),
                patch.object(monitor, "SYNC_INFO_FILE", sync_file),
                patch.object(monitor, "sync_catalog_repositories",
                             return_value=([project], {"repositories": 1})),
                patch.object(monitor, "scan_supported_folders",
                             return_value=([], [])),
                patch.object(monitor, "fetch_latest_release",
                             return_value=(project, newer, None)),
                patch.object(monitor, "generate_html"),
                patch.object(monitor, "send_discord") as notify,
            ):
                self.assertEqual(monitor.main(), 0)
            saved = json.loads(history_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["version"], "1.7.3")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["github:dev/port"]["version"], "1.7.3")
            notify.assert_not_called()
            self.assertEqual(json.loads(snap_file.read_text(
                encoding="utf-8"))["releases"][0]["version"], "1.7.3")

    def test_dashboard_semantic_order_and_incomparables(self):
        self.assertEqual(compare_versions("1.2.0-beta2", "1.2.0-beta3"), -1)
        self.assertEqual(compare_versions("1.2.0-rc1", "1.2.0"), -1)
        self.assertEqual(compare_versions("1.2.0", "1.2.0-beta3"), 1)
        self.assertEqual(compare_versions("1.2.0-beta2", "1.2.0"), -1)
        self.assertEqual(compare_versions("1.2", "v1.2.0"), 0)
        self.assertIsNone(compare_versions("1.2.0", "continuous"))
        self.assertIsNone(compare_versions("1.2.0", "v1.2.0-multicore.2"))


if __name__ == "__main__":
    unittest.main()
