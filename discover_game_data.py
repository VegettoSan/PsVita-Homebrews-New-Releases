#!/usr/bin/env python3
"""Discover possible Game/Data Files references for PSVitaAlive ports.

This is a review-only index. Never download assets or change PSVitaAlive apps.
GitHub/GitLab project links come exclusively from the canonical catalog.
Reddit searches are manual links: its API needs approved OAuth access.
"""
from __future__ import annotations

import os
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from catalog_schema_v2_compat import to_v1

CATALOG_URL = os.getenv(
    "PSVITAALIVE_CATALOG_URL",
    "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json",
)
OUTPUT = Path("data/game_data_candidates.json")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "").strip()
TIMEOUT = 18
SCAN_LIMIT = 100
USER_AGENT = "PSVitaAlive-GameDataDiscovery/1.0 (VegettoSan/PSVitaAlive)"
ASSET_PATTERN = re.compile(
    r"(?:game[\s_.-]*files?|data[\s_.-]*files?|game[\s_.-]*data|"
    r"data[\s_.-]*pack|assets?[\s_.-]*pack|\.obb\b)", re.I
)
ARCHIVE_PATTERN = re.compile(r"\.(?:zip|7z|rar|tar|gz|obb|apk)$", re.I)
GUIDE_PATTERN = re.compile(
    r"(?:game[\s_-]*files?|data[\s_-]*files?|"
    r"original game|copy of the game|game assets)", re.I
)
LINK_TYPES = {"game files", "game file", "data files", "data file"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def catalog_items(catalog):
    if isinstance(catalog, list):
        return catalog
    if isinstance(catalog, dict):
        for key in ("apps", "items", "catalog"):
            if isinstance(catalog.get(key), list):
                return catalog[key]
    raise ValueError("El catálogo no contiene una lista de aplicaciones")


def has_data(record):
    """Game/Data links count even when classified as Download or Mirror by old data."""
    if str(record.get("data_url") or "").strip():
        return True
    for link in record.get("links") or []:
        if not isinstance(link, dict):
            continue
        link_type = str(link.get("type") or "").strip().lower()
        name = str(link.get("name") or "").strip().lower()
        url = str(link.get("url") or "").strip()
        if url and (link_type in LINK_TYPES or
                    (link_type in {"download", "mirror"} and name in LINK_TYPES)):
            return True
    return False


def repo_from_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    host = (parsed.hostname or "").lower()
    if host == "github.com" and len(parts) >= 2:
        owner, name = parts[:2]
        if re.fullmatch(r"[\w.-]+", owner) and re.fullmatch(r"[\w.-]+", name):
            return ("github", owner + "/" + name.removesuffix(".git"))
    if host == "gitlab.com" and len(parts) >= 2:
        if "-" in parts:
            parts = parts[:parts.index("-")]
        if len(parts) >= 2 and all(re.fullmatch(r"[\w.-]+", p) for p in parts):
            return ("gitlab", "/".join(parts).removesuffix(".git"))
    return None


def app_repos(record):
    urls = []
    for key in ("source_url", "release_page", "repository_url"):
        if isinstance(record.get(key), str):
            urls.append(record[key])
    for item in record.get("links") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").lower() in {
            "repository", "official website", "documentation"
        }:
            urls.append(item.get("url"))
    found = []
    for url in urls:
        repo = repo_from_url(url)
        if repo and repo not in found:
            found.append(repo)
    return found[:2]


def missing_ports(catalog):
    result = []
    for app in catalog_items(catalog):
        if not isinstance(app, dict) or app.get("category_id") != "ports":
            continue
        if has_data(app):
            continue
        app_id = str(app.get("id") or "").strip()
        name = str(app.get("name") or "").strip()
        if not app_id or not name:
            continue
        note = "No hay enlaces Game Files / Data Files registrados"
        details = str(app.get("requirements") or "") + " " + str(app.get("description") or "")
        if GUIDE_PATTERN.search(details):
            note = "El port menciona archivos externos, pero no tiene Game/Data Files"
        result.append({
            "id": app_id,
            "title_id": str(app.get("title_id") or ""),
            "name": name,
            "icon": str(app.get("icon") or ""),
            "reason": note,
            "repositories": [list(r) for r in app_repos(app)],
        })
    return sorted(result, key=lambda x: x["name"].casefold())


def headers(platform):
    result = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if platform == "github":
        result["Accept"] = "application/vnd.github+json"
        if GITHUB_TOKEN:
            result["Authorization"] = "Bearer " + GITHUB_TOKEN
    elif GITLAB_TOKEN:
        result["PRIVATE-TOKEN"] = GITLAB_TOKEN
    return result


def get_json(url, platform):
    response = requests.get(url, headers=headers(platform), timeout=TIMEOUT)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


def valid_page(url, platform, project):
    """Only link to a project on its hosting service, never directly to a binary."""
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    host = "github.com" if platform == "github" else "gitlab.com"
    return (parsed.scheme == "https" and parsed.hostname == host
            and not parsed.username and not parsed.password
            and parsed.path.lower().startswith("/" + project.lower() + "/"))


def search_project(platform, project):
    candidates = []
    if platform == "github":
        endpoint = "https://api.github.com/repos/" + quote(project, safe="/") + "/releases?per_page=4"
        releases = get_json(endpoint, platform)
    else:
        endpoint = ("https://gitlab.com/api/v4/projects/" +
                    quote(project, safe="") + "/releases?per_page=4")
        releases = get_json(endpoint, platform)
    if not isinstance(releases, list):
        raise ValueError("Respuesta de releases inesperada")
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name") or "")
        if platform == "github":
            page = release.get("html_url") or ""
            assets = release.get("assets") or []
            names = [str(a.get("name") or "") for a in assets if isinstance(a, dict)]
            body = str(release.get("body") or "")
            date = str(release.get("published_at") or "")
        else:
            page = "https://gitlab.com/" + project + "/-/releases/" + quote(tag, safe="")
            links = (release.get("assets") or {}).get("links") or []
            names = [str(a.get("name") or "") for a in links if isinstance(a, dict)]
            body = str(release.get("description") or "")
            date = str(release.get("released_at") or "")
        if not valid_page(page, platform, project):
            continue
        likely = [name for name in names if ARCHIVE_PATTERN.search(name) and
                  ASSET_PATTERN.search(name)]
        if likely:
            candidates.append({
                "source": "GitHub Releases" if platform == "github" else "GitLab Releases",
                "kind": "possible-archive",
                "title": "Posible paquete: " + ", ".join(likely[:3])[:160],
                "url": page,
                "date": date[:10],
            })
        elif GUIDE_PATTERN.search(body):
            candidates.append({
                "source": "GitHub Releases" if platform == "github" else "GitLab Releases",
                "kind": "instructions",
                "title": "Release con instrucciones de Game/Data Files (" + tag[:40] + ")",
                "url": page,
                "date": date[:10],
            })
    # Recent issues may point to official setup directions or community references.
    # Only request them when no release already provides evidence.
    if not candidates and platform == "github":
        issues_url = "https://api.github.com/repos/" + quote(project, safe="/") + "/issues?state=all&per_page=30&sort=updated"
        issues = get_json(issues_url, platform)
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict) or "pull_request" in issue:
                    continue
                title = str(issue.get("title") or "")
                body = str(issue.get("body") or "")[:2400]
                if not GUIDE_PATTERN.search(title + " " + body):
                    continue
                page = issue.get("html_url")
                if valid_page(page, platform, project):
                    candidates.append({
                        "source": "GitHub Issues",
                        "kind": "community-reference",
                        "title": title[:160],
                        "url": page,
                        "date": str(issue.get("updated_at") or "")[:10],
                    })
                if len(candidates) >= 3:
                    break
    unique = {}
    for candidate in candidates:
        unique[candidate["url"]] = candidate
    return list(unique.values())[:5]


def check_app(app):
    result = []
    errors = []
    for platform, project in app["repositories"]:
        try:
            result += search_project(platform, project)
        except (ValueError, requests.RequestException) as exc:
            errors.append(platform + ":" + project + " (" + type(exc).__name__ + ")")
    return result[:8], errors


def build_report(catalog, previous, scanner=check_app, timestamp=None):
    timestamp = timestamp or now_iso()
    missing = missing_ports(catalog)
    prior = {a["id"]: a for a in previous.get("apps", [])
             if isinstance(a, dict) and a.get("id")}
    # Scan least-recently checked entries first, rotating for large catalogs.
    ordered = sorted(missing, key=lambda a: (prior.get(a["id"], {}).get("checked_at", ""), a["id"]))
    selected = [a for a in ordered if a["repositories"]][:SCAN_LIMIT]
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = {pool.submit(scanner, app): app["id"] for app in selected}
        for future in as_completed(tasks):
            app_id = tasks[future]
            try:
                results[app_id] = future.result()
            except Exception as exc:
                errors.append(app_id + ": " + type(exc).__name__)
                results[app_id] = ([], [type(exc).__name__])
    new_links = []
    items = []
    for app in missing:
        old = prior.get(app["id"], {})
        previous_sources = old.get("candidates") if isinstance(old.get("candidates"), list) else []
        if app["id"] in results:
            latest, failures = results[app["id"]]
            errors += [app["id"] + ": " + e for e in failures]
            # Preserve prior valid evidence on temporary upstream errors.
            candidates = latest if not failures else (latest or previous_sources)
            checked_at = timestamp if not failures else old.get("checked_at", "")
        else:
            candidates = previous_sources
            checked_at = old.get("checked_at", "")
        seen = set()
        safe = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get("url")
            if not isinstance(url, str) or not url.startswith("https://") or url in seen:
                continue
            seen.add(url)
            safe.append(candidate)
        old_urls = {c.get("url") for c in previous_sources if isinstance(c, dict)}
        new = [c for c in safe if c["url"] not in old_urls]
        for entry in new:
            new_links.append({"name": app["name"], "url": entry["url"]})
        items.append({**app, "candidates": safe[:8], "checked_at": checked_at,
                      "new_urls": [c["url"] for c in new]})
    return {
        "schema_version": 1,
        "scanned_at": timestamp,
        "missing_count": len(items),
        "possible_matches": sum(bool(a["candidates"]) for a in items),
        "checked_this_run": len(selected),
        "new_matches": len(new_links),
        "apps": items,
        "errors": errors[:30],
    }, new_links


def main():
    response = requests.get(CATALOG_URL, timeout=45, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    catalog = to_v1(response.json())
    # Do not overwrite a previous report if catalog fetch or structure fails.
    if not isinstance(catalog_items(catalog), list):
        raise ValueError("Catálogo inválido")
    previous = load_json(OUTPUT, {})
    if not isinstance(previous, dict):
        previous = {}
    report, _new_links = build_report(catalog, previous)
    save_json(OUTPUT, report)
    # Game Data discoveries are shown only on the web dashboard.
    print("Game Data: " + str(report["missing_count"]) + " ports sin enlaces; "
          + str(report["possible_matches"]) + " con pistas; "
          + str(report["checked_this_run"]) + " comprobados en esta ejecución; "
          + str(report["new_matches"]) + " referencias nuevas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
