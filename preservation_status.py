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
    r"(?:^|[-_. ])(?:alpha|beta|rc\d*|preview|nightly|dev|test)(?:$|[-_. ])",
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


def assess_backup(
    *, release_tag: str, catalog_version: str, archive_urls: list[str], is_registered: bool,
) -> tuple[str, str]:
    """Return status and Spanish explanation; never claims hash/file verification."""
    if not is_registered:
        return "unknown", "No hay ficha asociada en PSVitaAlive para comprobar respaldos"

    if not archive_urls:
        return "missing", "No hay un enlace directo a un VPK de Archive.org en la ficha"

    versions = [filename_version(url) for url in archive_urls]
    has_unversioned = any(version is None for version in versions)
    # The catalog may already be newer than the monitored release.
    # Never treat a backup of an older upstream tag as the current version.
    candidates = [
        value for value in (
            parse_numeric_version(release_tag),
            parse_numeric_version(catalog_version),
        ) if value is not None
    ]
    target = max(candidates) if candidates else None

    if target is None:
        return "unversioned", "Existe enlace Archive.org, pero no se puede comparar la versión"

    if target in versions:
        return "matched", "VPK de Archive.org con la versión indicada en el nombre (archivo sin comprobar)"

    if has_unversioned:
        return "unversioned", "Hay VPK en Archive.org sin versión identificable; revisar si es la versión actual"

    if all(version < target for version in versions if version is not None):
        return "outdated", "Los VPK enlazados en Archive.org indican versiones anteriores"

    return "review", "Las versiones de los VPK enlazados no coinciden; revisar manualmente"
