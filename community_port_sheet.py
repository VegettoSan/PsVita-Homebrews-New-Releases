#!/usr/bin/env python3
"""Read the publicly shared PS Vita Ports Tracker without changing its contents.

Source: a community spreadsheet linked from the r/VitaPiracy thread.
This module does not access Reddit programmatically and never downloads game
files. Spreadsheet references are review leads, NOT verified redistribution.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from urllib.parse import urlparse

import requests

SHEET_ID = "1_nuD1Sr5sNCtZPjWMliR-fFZDvQToUtnMwhh_dJkNeE"
SHEET_GID = "1739999294"
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/" + SHEET_ID +
    "/edit?gid=" + SHEET_GID
)
SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/" + SHEET_ID +
    "/export?format=csv&gid=" + SHEET_GID
)
REDDIT_THREAD = (
    "https://www.reddit.com/r/VitaPiracy/comments/1xd0dai/"
    "full_list_of_ps_vita_ports_google_sheets/"
)
MAX_CSV_BYTES = 6 * 1024 * 1024
MAX_ROWS = 3000
MAX_URLS_PER_CELL = 6
URL_RE = re.compile(r"https?://[^\s,\"<>]+", re.I)
DATA_ARCHIVE = re.compile(
    r"(?:\.(?:zip|7z|rar|obb)(?:$|[?/#\s])|"
    r"(?:game[\s_%-]*files?|data[\s_%-]*files?|data[\s_%-]*pack))", re.I
)
NON_DATA = {"", "-", "—", "none", "missing", "wip", "tba", "soon", "not available"}


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(value.split())


def urls(text):
    if not isinstance(text, str):
        return []
    return [url.rstrip(").;") for url in URL_RE.findall(text)[:MAX_URLS_PER_CELL]]


def host(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme == "https" and not parsed.username and not parsed.password:
            return (parsed.hostname or "").lower()
    except ValueError:
        pass
    return ""


def repo_identity(url):
    """Match developer project URL to the canonical app, not a fuzzy title."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.hostname == "github.com" and len(parts) >= 2:
            if re.fullmatch(r"[\w.-]+", parts[0]) and re.fullmatch(r"[\w.-]+", parts[1]):
                return ("github", (parts[0] + "/" + parts[1].removesuffix(".git")).lower())
        if parsed.hostname == "gitlab.com" and len(parts) >= 2:
            if "-" in parts:
                parts = parts[:parts.index("-")]
            if len(parts) >= 2 and all(re.fullmatch(r"[\w.-]+", x) for x in parts):
                return ("gitlab", "/".join(parts).removesuffix(".git").lower())
    except ValueError:
        pass
    return None


def parse_sheet_csv(payload):
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("Google Sheet CSV demasiado grande o no válido")
    if payload.lstrip().lower().startswith(("<html", "<!doctype html")):
        raise ValueError("Google Sheets devolvió HTML en vez de CSV")
    reader = csv.reader(io.StringIO(payload))
    try:
        raw_headers = next(reader)
    except StopIteration:
        raise ValueError("Hoja sin cabecera") from None
    headings = [normalize(v) for v in raw_headers]
    def find_heading(*keys):
        return next((i for i, heading in enumerate(headings) if any(k in heading for k in keys)), None)
    name_idx = find_heading("game name")
    download_idx = find_heading("download link")
    project_idx = find_heading("github developer", "developer page")
    stage_idx = find_heading("development stage")
    verifier_idx = find_heading("last verified by")
    date_idx = find_heading("last verified date")
    if name_idx is None or download_idx is None or project_idx is None:
        raise ValueError("Cabeceras esperadas no encontradas en Google Sheet")
    records = []
    for row_no, row in enumerate(reader, start=2):
        if row_no > MAX_ROWS:
            break
        def cell(index):
            return row[index].strip() if index is not None and index < len(row) else ""
        name = cell(name_idx)
        download = cell(download_idx)
        if not name or normalize(download) in NON_DATA:
            continue
        download_links = [u for u in urls(download) if host(u)]
        if not download_links:
            continue
        # A VPK alone is not evidence of an independent Game Data package.
        archive_evidence = [
            u for u in download_links if DATA_ARCHIVE.search(u)
        ]
        if not archive_evidence:
            continue
        projects = set()
        for u in urls(cell(project_idx)):
            identity = repo_identity(u)
            if identity:
                projects.add(identity)
        if not projects:
            continue
        # A community row is a reference, not an independent file verification.
        records.append({
            "row": row_no,
            "name": name[:140],
            "projects": sorted(projects),
            "archive_hosts": sorted({host(u) for u in archive_evidence}),
            "stage": cell(stage_idx)[:90],
            "verified_by": cell(verifier_idx)[:100],
            "verified_date": cell(date_idx)[:25],
            "url": SHEET_URL + "&range=A" + str(row_no),
        })
    return records


def fetch_public_sheet():
    response = requests.get(SHEET_CSV_URL, timeout=25, headers={
        "User-Agent": "PSVitaAlive-GameData-References/1.0 (public read-only sheet)",
        "Accept": "text/csv,text/plain;q=0.9",
    })
    response.raise_for_status()
    if len(response.content) > MAX_CSV_BYTES:
        raise ValueError("Respuesta CSV demasiado grande")
    return parse_sheet_csv(response.content.decode("utf-8-sig"))


def match_apps(apps, rows):
    """Strong identity matching only; never select an unrelated port by name."""
    by_project = {}
    for app in apps:
        for platform, project in app.get("repositories", []):
            by_project.setdefault((platform, project.lower()), set()).add(app["id"])
    result = {}
    for row in rows:
        possible = set()
        for identity in row["projects"]:
            possible.update(by_project.get(tuple(identity), set()))
        # Shared repositories map to multiple apps: avoid an ambiguous match.
        if len(possible) != 1:
            continue
        app_id = next(iter(possible))
        app = next((a for a in apps if a["id"] == app_id), None)
        if not app:
            continue
        # Require exact normalized port name OR an unambiguous repo identity
        # with a common meaningful name token (avoid sibling ports).
        app_name = normalize(app["name"])
        row_name = normalize(row["name"])
        a_tokens = {w for w in app_name.split() if len(w) > 3 and w not in {"vita", "port"}}
        r_tokens = {w for w in row_name.split() if len(w) > 3 and w not in {"vita", "port"}}
        if app_name != row_name and not a_tokens.intersection(r_tokens):
            continue
        source_domains = ", ".join(row["archive_hosts"][:2])
        candidate = {
            "source": "PS Vita Ports Tracker (Reddit / Google Sheets)",
            "kind": "community-sheet",
            "title": "Hoja comunitaria: paquete de datos referenciado (" + source_domains[:90] + ")",
            "url": row["url"],
            "date": row["verified_date"],
            "verified_by": row["verified_by"],
            "evidence": "referencia-comunitaria",
        }
        result.setdefault(app_id, []).append(candidate)
    return {k: v[:2] for k, v in result.items()}
