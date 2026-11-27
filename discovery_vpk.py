"""Bounded VPK validation for new discoveries, without executing or unpacking files."""
import hashlib
import re
import stat
import tempfile
import time
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit, unquote, urljoin
import requests
from discovery_enrichment import sfo_title_id

MAX_BYTES = 64 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com",
                  "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


def select_vpk(entry, releases):
    """Same unique asset and repository/tag provenance rule as sync_release_updates."""
    if entry.get("platform") != "github":
        return {"state": "review", "reason": "Pilot binary download supports GitHub releases only"}
    app = entry.get("app_draft") or {}
    urls = {l.get("url") for l in app.get("links", []) if isinstance(l, dict) and l.get("type") == "Download"}
    matches = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        assets = [a for a in release.get("assets") or [] if isinstance(a, dict)
                  and str(a.get("name") or "").lower().endswith(".vpk")]
        if not any(a.get("browser_download_url") in urls for a in assets):
            continue
        if len(assets) != 1:
            return {"state": "review", "reason": "Multiple VPK variants"}
        a = assets[0]
        url = a.get("browser_download_url")
        u = urlsplit(url)
        parts = [unquote(x) for x in u.path.split("/") if x]
        if (u.scheme != "https" or u.hostname != "github.com" or u.username or u.password or u.port
            or u.query or u.fragment or len(parts) != 6 or "/".join(parts[:2]).casefold() != entry["project"].casefold()
            or parts[2:4] != ["releases", "download"] or parts[4] != release.get("tag_name")
            or parts[5] != a.get("name") or not parts[5].lower().endswith(".vpk")):
            return {"state": "review", "reason": "Untrusted asset URL"}
        size = a.get("size")
        if type(size) is not int or size <= 0 or a.get("state") not in (None, "uploaded"):
            return {"state": "review", "reason": "Asset not uploaded or missing size"}
        matches.append({"state": "selected", "url": url, "size": size, "digest": a.get("digest"),
                        "tag": release.get("tag_name"), "asset_id": a.get("id")})
    return matches[0] if len(matches) == 1 else {"state": "review", "reason": "No unique matching published VPK asset"}


def validate_package(file, expected="", deadline=None):
    """Validate ZIP structure, CRC, and the bounded SFO identity; no extraction."""
    try:
        with zipfile.ZipFile(file) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 10000 or len(set(names)) != len(names) or names.count("sce_sys/param.sfo") != 1 or names.count("eboot.bin") != 1:
                raise ValueError("Missing or duplicate required ZIP entries")
            if sum(i.file_size for i in infos) > MAX_UNPACKED:
                return {"state": "deferred", "reason": "Uncompressed package exceeds pilot budget"}
            for i in infos:
                name = i.filename
                p = PurePosixPath(name)
                if (p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name
                    or i.flag_bits & 1 or stat.S_ISLNK(i.external_attr >> 16)):
                    raise ValueError("Unsafe or encrypted ZIP entry")
            sfo = z.getinfo("sce_sys/param.sfo")
            if not 20 <= sfo.file_size <= 65536 or z.getinfo("eboot.bin").file_size <= 0:
                raise ValueError("Invalid required entry size")
            with z.open(sfo) as src:
                tid = sfo_title_id(src.read(65537))
            if not tid or (expected and expected != tid):
                raise ValueError("Invalid or mismatched internal Title ID")
            for i in infos:
                if i.is_dir():
                    continue
                with z.open(i) as src:
                    while src.read(262144):
                        if deadline is not None and time.monotonic() >= deadline:
                            return {"state": "deferred", "reason": "Package verification time budget"}
            return {"state": "verified", "title_id": tid, "crc_checked": True}
    except (ValueError, OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, EOFError) as exc:
        return {"state": "invalid", "reason": type(exc).__name__ + ": " + str(exc)[:160]}


def verify_download(asset, expected="", deadline=None, byte_budget=MAX_BYTES, media_dir=None):
    if asset.get("state") != "selected":
        return asset
    size = asset["size"]
    if size > min(MAX_BYTES, byte_budget):
        return {"state": "deferred", "reason": "VPK exceeds pilot download budget"}
    deadline = min(deadline or float("inf"), time.monotonic() + 45)
    url = asset["url"]
    total = 0
    try:
        with requests.Session() as session, tempfile.TemporaryFile() as file:
            session.trust_env = False
            for _ in range(6):
                u = urlsplit(url)
                if u.scheme != "https" or u.hostname not in DOWNLOAD_HOSTS or u.username or u.password or u.port:
                    return {"state": "review", "reason": "Untrusted download redirect", "bytes_read": total}
                if time.monotonic() >= deadline:
                    return {"state": "deferred", "reason": "Download time budget", "bytes_read": total}
                with session.get(url, stream=True, allow_redirects=False, timeout=(5, 10),
                                 headers={"Accept-Encoding": "identity"}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["Location"])
                        continue
                    if response.status_code != 200:
                        return {"state": "retry", "reason": "HTTP " + str(response.status_code), "bytes_read": total}
                    digest = hashlib.sha256()
                    for chunk in response.iter_content(262144):
                        total += len(chunk)
                        if total > size or total > byte_budget or time.monotonic() >= deadline:
                            return {"state": "deferred", "reason": "Download size/time budget or size mismatch", "bytes_read": total}
                        digest.update(chunk)
                        file.write(chunk)
                    if total != size:
                        return {"state": "invalid", "reason": "Asset size mismatch", "bytes_read": total}
                    sha = digest.hexdigest()
                    if asset.get("digest") and asset["digest"] != "sha256:" + sha:
                        return {"state": "invalid", "reason": "Official digest mismatch", "bytes_read": total}
                    file.seek(0)
                    result = validate_package(file, expected, deadline)
                    if result.get("state") == "verified" and media_dir is not None:
                        from discovery_vpk_artwork import extract_artwork
                        try:
                            file.seek(0)
                            result["artwork"] = extract_artwork(file, sha, media_dir)
                        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                            result["artwork_error"] = type(exc).__name__
                    return {**result, "bytes_read": total, "sha256": sha, "asset_id": asset.get("asset_id"),
                            "tag": asset.get("tag"), "url": asset["url"]}
            return {"state": "review", "reason": "Too many download redirects", "bytes_read": total}
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        return {"state": "retry", "reason": type(exc).__name__, "bytes_read": total}
