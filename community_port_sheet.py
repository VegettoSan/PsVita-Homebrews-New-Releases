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


PLATFORM_SUFFIX = re.compile(r"\s*\((?:android|ios|mobile|pc|windows|gamecube|ps2|psp)\)\s*$", re.I)
TITLE_IDENTITY_PREFIX = (
    r"(?:port|porting|ported|wrapper|remake|reimplementation|"
    r"adaptation|adaptacion|conversion|recreation|version)"
    r"\s+(?:of|for|de|del)\s+(?:the\s+|el\s+|la\s+)?"
)


def title_variants(name):
    """Keep the whole title, also allowing an explicitly labelled platform."""
    main = str(name or "").strip()
    variants = {comparable_name(main)}
    without_platform = PLATFORM_SUFFIX.sub("", main)
    if without_platform != main:
        variants.add(comparable_name(without_platform))
    return {v for v in variants if len(v) >= 5}


def description_name_match(name, details):
    """Find an original game named as the port's SUBJECT, not a comparison.

    Requiring "port of X" etc. avoids conflating references like
    "inspired by X", "similar to X", or a list of supported games.
    """
    if not isinstance(details, dict):
        return None
    for key, label in (("description", "short-description"),
                       ("long_description", "long-description")):
        content = normalize(details.get(key, ""))
        if not content:
            continue
        for variant in sorted(title_variants(name), key=len, reverse=True):
            title = re.escape(variant)
            if re.search(r"\b" + TITLE_IDENTITY_PREFIX + title + r"(?:\b|$)", content):
                return label
    return None


def fetch_public_sheet():
    response = requests.get(SHEET_CSV_URL, timeout=25, headers={
        "User-Agent": "PSVitaAlive-GameData-References/1.0 (public read-only sheet)",
        "Accept": "text/csv,text/plain;q=0.9",
    })
    response.raise_for_status()
    if len(response.content) > MAX_CSV_BYTES:
        raise ValueError("Respuesta CSV demasiado grande")
    return parse_sheet_csv(response.content.decode("utf-8-sig"))


def match_apps(apps, rows, descriptions=None):
    """Correlate sheet rows with canonical ports using independent evidence.

    Use matching repos first, then a unique exact name, then descriptions
    explicitly identifying the ORIGINAL ported game. Mere mentions of a
    game (comparisons, dependencies) never establish an identity.
    """
    descriptions = descriptions or {}
    by_project = {}
    by_name = {}
    by_id = {app["id"]: app for app in apps}
    for app in apps:
        for platform, project in app.get("repositories", []):
            by_project.setdefault((platform, project.lower()), set()).add(app["id"])
        key = comparable_name(app["name"])
        if len(key) >= 6:
            by_name.setdefault(key, set()).add(app["id"])

    projects_in_rows = Counter(
        project for row in rows for project in
        {tuple(p) for p in row.get("projects", [])}
    )
    result = {}
    for row in rows:
        projects = {tuple(p) for p in row.get("projects", [])}
        repo_matches = set()
        for identity in projects:
            repo_matches.update(by_project.get(identity, set()))

        # Evaluate the short/long description of EVERY missing port. Only
        # a unique explicit "port of X" match can introduce an alias.
        desc_matches = {
            app_id: method for app_id in by_id
            if (method := description_name_match(
                row["name"], descriptions.get(app_id, {})))
        }
        match_method = None
        app_id = None
        if len(repo_matches) == 1:
            candidate_id = next(iter(repo_matches))
            # A repo reused for multiple sheet entries needs additional
            # evidence. A unique repo gets its usual strong correspondence.
            unique_sheet_project = any(
                projects_in_rows[p] == 1 for p in projects
                if candidate_id in by_project.get(p, set())
            )
            if unique_sheet_project or name_related(by_id[candidate_id]["name"], row["name"]):
                app_id = candidate_id
                match_method = "repository"
            elif candidate_id in desc_matches:
                app_id = candidate_id
                match_method = "repository-" + desc_matches[candidate_id]
        elif len(repo_matches) > 1:
            alike = [candidate_id for candidate_id in repo_matches
                     if name_related(by_id[candidate_id]["name"], row["name"])]
            if len(alike) == 1:
                app_id = alike[0]
                match_method = "repository-title"
            else:
                descriptive = [candidate_id for candidate_id in repo_matches
                               if candidate_id in desc_matches]
                if len(descriptive) == 1 and len(desc_matches) == 1:
                    app_id = descriptive[0]
                    match_method = "repository-" + desc_matches[app_id]

        if match_method is None:
            exact = by_name.get(comparable_name(row["name"]), set())
            if len(exact) == 1 and len(comparable_name(row["name"])) >= 8:
                candidate_id = next(iter(exact))
                catalog_repos = {(p, n.lower()) for p, n in
                                 by_id[candidate_id].get("repositories", [])}
                if not (projects and catalog_repos and
                        not projects.intersection(catalog_repos)):
                    app_id = candidate_id
                    match_method = "exact-title"

        if match_method is None and len(desc_matches) == 1:
            candidate_id, method = next(iter(desc_matches.items()))
            # If a known repo points to a DIFFERENT catalog port, the
            # description alone must not override that stronger identity.
            if not repo_matches or candidate_id in repo_matches:
                app_id = candidate_id
                match_method = method

        if match_method is None:
            continue

        source_domains = ", ".join(row["archive_hosts"][:2])
        is_archive = row.get("evidence_type") == "archive"
        evidence_needs_review = (match_method.endswith("description") or
                                 "description" in match_method)
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
            "sheet_title": row["name"][:140],
            "identity_review": evidence_needs_review,
        }
        result.setdefault(app_id, []).append(candidate)
    return {k: v[:3] for k, v in result.items()}
