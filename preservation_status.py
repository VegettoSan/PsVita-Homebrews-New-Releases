#!/usr/bin/env python3
"""Conservative Archive.org preservation hints for PSVitaAlive release monitor.

Only catalog Download/Mirror URLs to .vpk are inspected. Filenames are evidence
of *declared* versions, NOT proof of a healthy Archive.org copy. Legacy VPKs
without versions are "unversioned", not silently reported as missing.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_VERSION = re.compile(r"^[vV]?\.?\s*(\d{1,4}(?:\.\d{1,4}){1,3})$")
# A version must be separated from the app name: 2048.vpk and z2.vpk
# must NOT be mistaken for version 2048 or 2.
_FILENAME_VERSION = re.compile(
    r"(?:^|(?<=[ _.(\-]))v?\.?(\d{1,4}(?:\.\d{1,4}){1,3})(?=$|[ _.)-])",
    re.IGNORECASE,
)
_BAD_SUFFIX = re.compile(
    r"(?:^|[-_. ])(?:alpha|beta|rc\d*|preview|nightly|dev|test|hotfix)(?:$|[-_. ])",
    re.IGNORECASE,
)


def parse_numeric_version(value: object) -> tuple[int, ...] | None:
    match = _VERSION.fullmatch(str(value or "").strip())
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def archive_vpk_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"archive.org", "www.archive.org"}:
        return False
    path = unquote(parsed.path)
    pieces = path.split("/")
    return len(pieces) >= 4 and pieces[1].lower() == "download" and bool(pieces[2]) and path.lower().endswith(".vpk")


def find_archive_vpk_urls(links: object) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    if not isinstance(links, list):
        return urls
    for item in links:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if archive_vpk_url(url) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def filename_version(url: str) -> tuple[int, ...] | None:
    if not archive_vpk_url(url):
        return None
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])[:-4]
    if _BAD_SUFFIX.search(filename):
        return None
    versions = {parse_numeric_version(m.group(1)) for m in _FILENAME_VERSION.finditer(filename)}
    versions.discard(None)
    return next(iter(versions)) if len(versions) == 1 else None


def current_download(links: object) -> dict | None:
    """Return a unique, current Download link from the canonical app record."""
    if not isinstance(links, list):
        return None
    recommended = [link for link in links if isinstance(link, dict) and link.get("recommended") is True]
    if len(recommended) > 1:
        return None
    if recommended:
        return recommended[0] if str(recommended[0].get("type") or "").lower() == "download" else None
    downloads = [link for link in links if isinstance(link, dict)
                 and str(link.get("type") or "").lower() == "download"]
    return downloads[0] if len(downloads) == 1 else None


def assess_backup(
    *, release_tag: str, catalog_version: str, links: object, is_registered: bool,
) -> tuple[str, str]:
    """Assess *current recommended VPK*, not historical mirrors.

    This is a URL-based catalog indicator. It does not query Archive.org or
    imply that files are available or have cryptographically verified contents.
    """
    if not is_registered:
        return "unknown", "Sin ficha asociada en PSVitaAlive"

    selected = current_download(links)
    if selected is None:
        return "review", "No se puede identificar una única descarga principal"

    url = selected.get("url")
    if not archive_vpk_url(url):
        if find_archive_vpk_urls(links):
            return "missing", "La descarga actual no usa Archive.org; hay un respaldo anterior en otros enlaces"
        return "missing", "La descarga actual no apunta a un VPK de Archive.org"

    # In PSVitaAlive, maintainers switch the main Download to Archive.org
    # when they have uploaded the currently catalogued version.
    # If the upstream release is newer, that Archive link remains an older
    # catalog version, even if its VPK has no version in the filename.
    upstream_version = parse_numeric_version(release_tag)
    current_version = parse_numeric_version(catalog_version)
    if release_tag and upstream_version is None:
        return "review", "La versión de GitHub/GitLab es ambigua; revisar respaldo de Archive.org"
    if upstream_version is not None and current_version is not None and upstream_version > current_version:
        return "outdated", "Archive.org enlaza la versión del catálogo, pero ya hay otra versión más reciente"

    file_ver = filename_version(url)
    if file_ver is not None and current_version is not None and file_ver != current_version:
        return "review", "El nombre del VPK en Archive.org no coincide con la versión del catálogo"

    if file_ver is None:
        return "matched", "Descarga principal en Archive.org; VPK antiguo sin versión en el nombre (no verificado)"
    return "matched", "Descarga principal en Archive.org con nombre de versión coincidente (no verificado)"
