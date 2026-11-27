import io
import struct
import unittest
import zipfile
from discovery_author_profiles import author_proposal, DEFAULT_AVATAR
from discovery_vpk import select_vpk, validate_package, verify_download, latest_publication_asset


class AuthorTests(unittest.TestCase):
    entry = {"author_profile": "https://github.com/TestDev"}
    owner = {"url": "https://github.com/TestDev", "id": 42, "fork": False, "group": False}
    registry = {"authors": {"ids": []}}

    def test_public_profile_draft_matches_web(self):
        def request(url):
            self.assertEqual(url, "https://api.github.com/users/testdev")
            return {"id": 42, "login": "TestDev", "type": "User", "html_url": self.owner["url"]}
        result = author_proposal(self.entry, self.owner, [], self.registry, request)
        self.assertEqual(result["state"], "draft")
        self.assertEqual(result["draft"], {"id": "testdev", "name": "TestDev", "bio": "Developer",
            "avatar": DEFAULT_AVATAR, "links": [{"type": "Repository", "name": "GitHub",
                "url": "https://github.com/testdev", "recommended": True}]})

    def test_existing_account_reuses_id(self):
        author = {"id": "existing", "links": [{"url": "https://github.com/testdev/project"}]}
        result = author_proposal(self.entry, self.owner, [author], self.registry, None)
        self.assertEqual(result, {"state": "existing", "author_ids": ["existing"]})

    def test_fork_or_owner_mismatch(self):
        for owner in [{**self.owner, "fork": True}, {**self.owner, "url": "https://github.com/other"}]:
            self.assertEqual(author_proposal(self.entry, owner, [], self.registry, None)["state"], "review")

    def test_retired_slug(self):
        profile = {"id": 42, "login": "TestDev", "type": "User", "html_url": self.owner["url"]}
        self.assertEqual(author_proposal(self.entry, self.owner, [], {"authors": {"ids": ["testdev"]}},
                         lambda url: profile)["state"], "review")


def package(tid="DEMO00001", eboot=True, extra=None):
    raw = (struct.pack("<4sIIII", b"\0PSF", 0x101, 36, 48, 1)
           + struct.pack("<HHIII", 0, 0x0204, 10, 12, 0)
           + b"TITLE_ID\0\0\0\0" + tid.encode() + b"\0\0\0")
    file = io.BytesIO()
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("sce_sys/param.sfo", raw)
        if eboot:
            z.writestr("eboot.bin", b"synthetic executable fixture")
        if extra:
            z.writestr(extra, b"x")
    file.seek(0)
    return file


class VpkTests(unittest.TestCase):
    url = "https://github.com/dev/demo/releases/download/v1/demo.vpk"
    entry = {"platform": "github", "project": "dev/demo", "app_draft":
        {"links": [{"type": "Download", "url": url}]}}
    asset = {"name": "demo.vpk", "browser_download_url": url, "size": 1234, "state": "uploaded"}

    def test_unique_release_asset(self):
        release = {"tag_name": "v1", "assets": [self.asset]}
        self.assertEqual(select_vpk(self.entry, [release])["state"], "selected")
        release["assets"].append({**self.asset, "name": "other.vpk"})
        self.assertEqual(select_vpk(self.entry, [release])["state"], "review")

    def test_wrong_repository_or_tag(self):
        self.assertEqual(select_vpk(self.entry, [{"tag_name": "v2", "assets": [self.asset]}])["state"], "review")
        self.assertEqual(select_vpk({**self.entry, "project": "other/demo"},
                         [{"tag_name": "v1", "assets": [self.asset]}])["state"], "review")

    def test_latest_stable_release_refresh_and_attribution(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        entry = {**self.entry, "key": "gh:123", "author_profile": "https://github.com/dev"}
        repo = {"id": 123, "full_name": "dev/demo", "fork": False, "owner": {"login": "dev", "type": "User"}}
        asset = {**self.asset, "browser_download_url": self.url.replace("/v1/", "/v2/")}
        latest = {"draft": False, "prerelease": False, "tag_name": "v2", "published_at": "2026-11-26T00:00:00Z", "assets": [asset]}
        proof = latest_publication_asset(entry, repo, latest, now)
        self.assertEqual(proof["state"], "verified")
        self.assertEqual(proof["draft_update"]["version"], "2")
        self.assertEqual(proof["asset"]["tag"], "v2")
        self.assertEqual(latest_publication_asset(entry, repo, {**latest, "prerelease": True}, now)["state"], "WAITING_RELEASE")
        self.assertEqual(latest_publication_asset(entry, {**repo, "owner": {"login": "dev", "type": "Organization"}}, latest, now)["state"], "NEEDS_REVIEW")
        self.assertEqual(latest_publication_asset(entry, repo, {**latest, "assets": [asset, asset]}, now)["state"], "WAITING_RELEASE")

    def test_valid_package_identity(self):
        self.assertEqual(validate_package(package(), "DEMO00001"),
                         {"state": "verified", "title_id": "DEMO00001", "crc_checked": True,
                          "display_name": "", "display_name_field": ""})

    def test_invalid_package_or_identity(self):
        for data, expected in [(package(), "OTHER0001"), (package(eboot=False), ""),
                               (package(extra="../evil"), ""), (io.BytesIO(b"<html>404</html>"), "")]:
            self.assertEqual(validate_package(data, expected)["state"], "invalid")

    def test_budget_defers_without_download(self):
        self.assertEqual(verify_download({"state": "selected", "size": 100}, byte_budget=10)["state"], "deferred")


if __name__ == "__main__":
    unittest.main()
