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
    """Capture usable downloadable packages; no source binaries are fetched.

    The community author reports that downloads generally bundle VPK and data,
    so non-archive download links are kept as *unverified community packages*,
    never silently labelled as standalone Game Data.
    """
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
        return next((i for i, h in enumerate(headings) if any(k in h for k in keys)), None)

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

        # Do not mistake source-code repos and documentation links for game data.
        package_links = [u for u in download_links if
                         host(u) not in {"docs.google.com", "www.reddit.com"} and
                         not (host(u) in {"github.com", "gitlab.com"} and
                              ("/issues/" in urlparse(u).path or
                               "/blob/" in urlparse(u).path or
                               "/tree/" in urlparse(u).path))]
        if not package_links:
            continue
        is_archive = any(DATA_ARCHIVE.search(u) for u in package_links)
        projects = set()
        for u in urls(cell(project_idx)):
            identity = repo_identity(u)
            if identity:
                projects.add(identity)
        # A row with no repository can still be linked by a single exact
        # normalized title, but will be explicitly flagged for manual review.
        records.append({
            "row": row_no,
            "name": name[:140],
            "projects": sorted(projects),
            "archive_hosts": sorted({host(u) for u in package_links}),
            "evidence_type": "archive" if is_archive else "bundle",
            "stage": cell(stage_idx)[:90],
            "verified_by": cell(verifier_idx)[:100],
            "verified_date": cell(date_idx)[:25],
            "url": SHEET_URL + "&range=A" + str(row_no),
        })
    return records


def comparable_name(value):
    """Normalize innocuous PS Vita suffixes without conflating game titles."""
    pieces = normalize(value).split()
    prefixes = {"psvita", "vita", "ps", "port"}
    pieces = [p for p in pieces if p not in prefixes]
    return " ".join(pieces)


def name_related(a, b):
    """Require an exact name or at least two overlapping meaningful tokens."""
    a_norm = comparable_name(a)
    b_norm = comparable_name(b)
    if a_norm == b_norm and len(a_norm) >= 6:
        return True
    a_parts = {p for p in a_norm.split() if len(p) >= 4}
    b_parts = {p for p in b_norm.split() if len(p) >= 4}
    return len(a_parts & b_parts) >= 2


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
    """Match by developer repository, or uniquely by exact normalized title.

    A shared repository only maps when the game title is distinct. A
    repository attached to multiple sheet rows is NOT enough on its own.
    """
    by_project = {}
    by_name = {}
    by_id = {app["id"]: app for app in apps}
    for app in apps:
        for platform, project in app.get("repositories", []):
            by_project.setdefault((platform, project.lower()), set()).add(app["id"])
        key = comparable_name(app["name"])
        if len(key) >= 6:
            by_name.setdefault(key, set()).add(app["id"])

    projects_in_rows = Counter(project for row in rows for project in
                               set(tuple(p) for p in row.get("projects", [])))
    result = {}
    for row in rows:
        repo_matches = set()
        projects = {tuple(p) for p in row.get("projects", [])}
        for identity in projects:
            repo_matches.update(by_project.get(identity, set()))

        match_method = None
        if len(repo_matches) == 1:
            app_id = next(iter(repo_matches))
            # If a single repo is shared by multiple sheet games, require
            # unambiguous similarity to avoid attaching data to wrong ports.
            unique_sheet_project = any(projects_in_rows[p] == 1 for p in projects
                                       if app_id in by_project.get(p, set()))
            if unique_sheet_project or name_related(by_id[app_id]["name"], row["name"]):
                match_method = "repository"
        elif len(repo_matches) > 1:
            alike = [app_id for app_id in repo_matches
                     if name_related(by_id[app_id]["name"], row["name"])]
            if len(alike) == 1:
                app_id = alike[0]
                match_method = "repository-title"

        if not match_method:
            # Name-only fallback is safe only if exactly one missing-data port
            # has precisely the same normalized full title.
            matches = by_name.get(comparable_name(row["name"]), set())
            if len(matches) == 1 and len(comparable_name(row["name"])) >= 8:
                app_id = next(iter(matches))
                # If developer repos disagree, do not silently conflate games.
                catalog_repos = {(p, n.lower()) for p, n in
                                 by_id[app_id].get("repositories", [])}
                if projects and catalog_repos and not projects.intersection(catalog_repos):
                    continue
                match_method = "exact-title"

        if not match_method:
            continue

        source_domains = ", ".join(row["archive_hosts"][:2])
        is_archive = row.get("evidence_type") == "archive"
        candidate = {
            "source": "PS Vita Ports Tracker (Reddit / Google Sheets)",
            "kind": "community-sheet",
            "title": (
                ("Hoja comunitaria: posible archivo de datos (" if is_archive
                 else "Hoja comunitaria: descarga de port con datos anunciados (")
                + source_domains[:85] + ")"
            ),
            "url": row["url"],
            "date": row["verified_date"],
            "verified_by": row["verified_by"],
            "evidence": "referencia-comunitaria",
            "evidence_type": "archive" if is_archive else "bundle",
            "match_method": match_method,
        }
        result.setdefault(app_id, []).append(candidate)
    return {k: v[:3] for k, v in result.items()}
