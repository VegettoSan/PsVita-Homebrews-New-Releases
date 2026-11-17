"""Read-only Game Data leads from an optional public app snapshot.

Only Vita homebrew ports with unique Title IDs are eligible. A file URL is
unverified evidence, not an installation instruction or redistribution approval.
No external records replace the canonical PSVitaAlive catalog.
"""
from __future__ import annotations

import ipaddress
import json
import os
from collections import Counter
from urllib.parse import unquote, urlsplit

import requests

SNAPSHOT_URL = os.getenv(
    "EXTERNAL_GAME_DATA_SNAPSHOT_URL",
    "https://vitaforge.josephinoo.dev/api/v1/apps/snapshot",
)
SOURCE_LABEL = "Catálogo público"
KIND = "catalog-archive"
MAX_BYTES = 40 * 1024 * 1024
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz", ".obb")


def archive_url(url):
    """Accept only public HTTPS archive links, never arbitrary package URLs."""
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if (p.scheme != "https" or not host or p.username or p.password
                or p.port not in (None, 443) or host == "localhost"
                or host.endswith((".localhost", ".local", ".internal"))):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return unquote(p.path).lower().endswith(ARCHIVE_SUFFIXES)
    except (ValueError, UnicodeError):
        return False


def fetch_snapshot():
    """Fetch and bound a live JSON snapshot; return only its app records."""
    headers = {"Accept": "application/json", "User-Agent": "PSVitaAlive-GameDataDiscovery/1.0"}
    with requests.get(SNAPSHOT_URL, timeout=(12, 90), headers=headers, stream=True) as response:
        response.raise_for_status()
        limit = response.headers.get("Content-Length", "")
        if limit.isdigit() and int(limit) > MAX_BYTES:
            raise ValueError("Snapshot excede el tamaño máximo")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("Snapshot excede el tamaño máximo")
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("El snapshot no contiene data[]")
    if len(payload["data"]) > 100_000:
        raise ValueError("Demasiados registros en el snapshot")
    return payload["data"]


def _positive_size(value):
    try:
        size = int(value)
        if 0 < size < 200_000_000_000:
            return size
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _candidate(url, *, size=None, language="", date="", variant="general"):
    if not archive_url(url):
        return None
    lang = str(language or "").strip().lower()[:16]
    if lang and (len(lang) > 12 or not all(c.isalpha() or c in "-_" for c in lang)):
        lang = ""
    title = "Posible archivo Game Data" + (" (" + lang + ")" if lang else "")
    item = {
        "kind": KIND,
        "source": SOURCE_LABEL,
        "title": title,
        "url": url,
        "match_method": "title-id",
        "evidence": "Enlace de archivo en catálogo público; contenido no verificado",
        "variant": variant,
    }
    if lang:
        item["language"] = lang
    amount = _positive_size(size)
    if amount is not None:
        item["size"] = amount
    if isinstance(date, str) and len(date) >= 10 and date[:4].isdigit():
        item["date"] = date[:10]
    return item


def match_apps(missing, records):
    """Match only unique Title IDs; never rely on numeric IDs or fuzzy names."""
    if not isinstance(records, list):
        raise ValueError("Se esperaba una lista de aplicaciones")
    wanted = {str(a.get("title_id") or "").strip().upper(): a for a in missing
              if isinstance(a, dict) and str(a.get("title_id") or "").strip()
              and a.get("id")}
    grouped = {}
    counts = Counter()
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("type") != "app" or str(record.get("category") or "").casefold() != "port":
            continue
        tid = str(record.get("title_id") or "").strip().upper()
        if tid not in wanted:
            continue
        counts[tid] += 1
        grouped[tid] = record
    result = {}
    for tid, record in grouped.items():
        if counts[tid] != 1:
            continue
        candidates = []
        date = record.get("release_date") or record.get("updated_at") or ""
        for pkg in record.get("packages") or []:
            if not isinstance(pkg, dict) or str(pkg.get("kind") or "").casefold() not in {
                    "game_files", "data_files"}:
                continue
            c = _candidate(pkg.get("url"), size=pkg.get("size"), date=date)
            if c:
                candidates.append(c)
        c = _candidate(record.get("data_url"), size=record.get("data_size"), date=date)
        if c:
            candidates.append(c)
        for variant in record.get("game_data_languages") or []:
            if not isinstance(variant, dict):
                continue
            c = _candidate(variant.get("url"), size=variant.get("size"),
                           language=variant.get("language"), date=date,
                           variant="language")
            if c:
                candidates.append(c)
        deduped = []
        seen = set()
        for c in candidates:
            if c["url"] not in seen:
                deduped.append(c)
                seen.add(c["url"])
        if deduped:
            result[wanted[tid]["id"]] = deduped[:5]
    return result
