#!/usr/bin/env python3
"""Incremental PS Vita homebrew discovery. Produces review-only JSON drafts."""
import base64
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from catalog_schema_v2_compat import to_v1

BASE = Path("data/discovery")
STATE = BASE / "state.json"
CANDIDATES = BASE / "candidates.json"
DRAFTS = Path("docs/discovery-drafts")
CATALOG_URL = os.getenv("PSVITAALIVE_CATALOG_URL", "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json")
AUTHORS_URL = os.getenv("PSVITAALIVE_AUTHORS_URL", "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/authors.json")
PROFILE_LIMIT = max(1, min(8, int(os.getenv("DISCOVERY_PROFILE_LIMIT", "3"))))
REPO_LIMIT = max(1, min(30, int(os.getenv("DISCOVERY_REPO_LIMIT", "18"))))
QUERIES = ("topic:ps-vita", '"ps vita" in:name,description,readme',
           "vitasdk in:name,description,readme", "psvita in:name,description,readme",
           "vita2d in:readme", '"vita homebrew" in:readme')
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "PSVitaAlive-Discovery/1.0"}
if os.getenv("GITHUB_TOKEN"):
    HEADERS["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
GL_HEADERS = {"User-Agent": "PSVitaAlive-Discovery/1.0"}
if os.getenv("GITLAB_TOKEN"):
    GL_HEADERS["PRIVATE-TOKEN"] = os.environ["GITLAB_TOKEN"]


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def due(date, days=14):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(date.replace("Z", "+00:00")) >= timedelta(days=days)
    except (AttributeError, TypeError, ValueError):
        return True


def read_json(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(url, params=None, gitlab=False, plain=False):
    headers = GL_HEADERS if gitlab else HEADERS
    response = requests.get(url, params=params, headers=headers, timeout=20)
    if response.status_code == 404:
        return None
    if response.status_code in (403, 429):
        raise RuntimeError("API rate limit: " + (urlparse(url).hostname or ""))
    response.raise_for_status()
    return response.text[:40000] if plain else response.json()


def project_url_key(value):
    try:
        u = urlparse(value)
        p = [x for x in u.path.split("/") if x]
        h = (u.hostname or "").lower().removeprefix("www.")
        if u.scheme != "https" or u.username or u.password:
            return ""
        if h == "github.com" and len(p) >= 2 and p[0] not in ("search", "topics", "orgs"):
            return "github:" + (p[0] + "/" + p[1].removesuffix(".git")).lower()
        if h == "gitlab.com" and len(p) >= 2 and p[0] not in ("users", "explore", "search"):
            if "-" in p:
                p = p[:p.index("-")]
            return "gitlab:" + "/".join(p).removesuffix(".git").lower()
    except (ValueError, TypeError):
        pass
    return ""


def official_repo_keys(catalog):
    keys = set()
    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in ("url", "source_url", "release_page", "repository", "repository_url", "repo_url") and isinstance(v, str):
                    key = project_url_key(v)
                    if key:
                        keys.add(key)
                elif isinstance(v, (dict, list)):
                    walk(v)
    walk(catalog)
    return keys


def profiles_from_authors(authors):
    found, existing = set(), {}
    for author in authors:
        if not isinstance(author, dict):
            continue
        for link in author.get("links", []):
            if not isinstance(link, dict):
                continue
            try:
                u = urlparse(link.get("url", ""))
                parts = [x for x in u.path.split("/") if x]
                host = (u.hostname or "").lower().removeprefix("www.")
                if u.scheme != "https" or len(parts) != 1 or host not in ("github.com", "gitlab.com"):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", parts[0]) or parts[0] in ("search", "explore", "topics", "orgs"):
                    continue
                key = ("github" if host == "github.com" else "gitlab") + ":" + parts[0].lower()
                found.add(key)
                existing.setdefault(key, []).append(author.get("id"))
            except (TypeError, ValueError):
                continue
    return sorted(found), existing


def short_text(value, maximum):
    if not isinstance(value, str):
        return ""
    cleaned = html.unescape(re.sub(r"<[^>]*>", " ", value))
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" #*-_")
    if len(cleaned) <= maximum:
        return cleaned
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    return sentence if 25 <= len(sentence) <= maximum else ""


def readme_paragraph(text):
    """Only a short prose paragraph, never the entire README."""
    paragraphs, lines, fenced = [], [], False
    for raw in text.splitlines()[:220]:
        line = raw.strip()
        if line.startswith("~~~") or line.startswith(chr(96) * 3):
            fenced = not fenced
            if lines:
                paragraphs.append(" ".join(lines))
                lines = []
            continue
        if fenced or not line or line.startswith(("#", ">", "|", "-", "*", "!", "[!", "<")):
            if lines:
                paragraphs.append(" ".join(lines))
                lines = []
            continue
        lines.append(line)
    if lines:
        paragraphs.append(" ".join(lines))
    for para in paragraphs:
        value = short_text(para, 420)
        if len(value) >= 35 and re.search(r"\b(vita|vitasdk|homebrew|game|port|plugin|app)\b", value, re.I):
            return value
    return ""


def repo_id(repo, platform):
    return ("gh:" if platform == "github" else "gl:") + str(repo["id"])


def project(repo, platform):
    return str(repo.get("full_name") if platform == "github" else repo.get("path_with_namespace") or "")


def compact_repo(repo, platform):
    """Store only fields required for analysis; do not persist full API responses."""
    fields = ("id", "name", "description", "full_name", "path_with_namespace",
              "default_branch", "topics", "archived", "disabled")
    obj = {k: repo[k] for k in fields if k in repo}
    if platform == "github":
        owner = repo.get("owner") or {}
        obj["owner"] = {"login": owner.get("login"), "avatar_url": owner.get("avatar_url")}
    else:
        namespace = repo.get("namespace") or {}
        obj["namespace"] = {"path": namespace.get("path"), "avatar_url": namespace.get("avatar_url")}
    return obj


def enqueue(state, repo, platform):
    if not repo.get("id") or not project(repo, platform) or repo.get("archived") or repo.get("disabled"):
        return
    key = repo_id(repo, platform)
    if key in state["seen"] or any(x["key"] == key for x in state["queue"]):
        return
    if len(state["queue"]) < 2500:
        state["queue"].append({"key": key, "platform": platform, "repo": compact_repo(repo, platform)})


def global_search(state):
    idx = state["query_index"] % len(QUERIES)
    page = state["query_page"]
    response = request("https://api.github.com/search/repositories",
                       {"q": QUERIES[idx], "sort": "updated", "per_page": 30, "page": page}) or {}
    rows = response.get("items", [])
    for repo in rows:
        enqueue(state, repo, "github")
    if len(rows) < 30 or page >= 3:
        state["query_index"], state["query_page"] = (idx + 1) % len(QUERIES), 1
    else:
        state["query_page"] += 1
    return len(rows)


def global_gitlab_search(state):
    """Rotate through public GitLab projects independently of GitHub searches."""
    terms = ("ps vita", "psvita", "vitasdk", "vita homebrew")
    idx = int(state.get("gl_query_index", 0)) % len(terms)
    page = int(state.get("gl_query_page", 1))
    rows = request("https://gitlab.com/api/v4/projects",
                   {"search": terms[idx], "per_page": 20, "page": page,
                    "simple": "true", "order_by": "last_activity_at", "sort": "desc"},
                   gitlab=True) or []
    for item in rows:
        enqueue(state, item, "gitlab")
    if len(rows) < 20 or page >= 3:
        state["gl_query_index"], state["gl_query_page"] = (idx + 1) % len(terms), 1
    else:
        state["gl_query_page"] = page + 1
    return len(rows)


def scan_profiles(state, profiles):
    scanned = 0
    if not profiles:
        return scanned
    for offset in range(len(profiles)):
        if scanned >= PROFILE_LIMIT:
            break
        idx = (state["profile_cursor"] + offset) % len(profiles)
        key = profiles[idx]
        if not due(state["profiles"].get(key)):
            continue
        platform, handle = key.split(":", 1)
        if platform == "github":
            repos = request("https://api.github.com/users/" + quote(handle) + "/repos",
                            {"per_page": 100, "sort": "updated", "type": "owner"}) or []
        else:
            users = request("https://gitlab.com/api/v4/users", {"username": handle}, gitlab=True) or []
            user = next((u for u in users if str(u.get("username", "")).lower() == handle), None)
            repos = request("https://gitlab.com/api/v4/users/" + str(user["id"]) + "/projects",
                            {"per_page": 100, "simple": "true"}, gitlab=True) or [] if user else []
        for repo in repos:
            enqueue(state, repo, platform)
        state["profiles"][key] = utc()
        scanned += 1
    state["profile_cursor"] = (state["profile_cursor"] + max(scanned, 1)) % len(profiles)
    return scanned


def readme(repo, platform):
    if platform == "github":
        obj = request("https://api.github.com/repos/" + project(repo, platform) + "/readme")
        if isinstance(obj, dict):
            return base64.b64decode(obj.get("content", "")).decode("utf-8", "replace")[:40000]
        return ""
    pid = quote(str(repo["id"]))
    return request("https://gitlab.com/api/v4/projects/" + pid + "/repository/files/README.md/raw",
                   {"ref": repo.get("default_branch") or "main"}, gitlab=True, plain=True) or ""


def files_for(repo, platform):
    if platform == "github":
        tree = request("https://api.github.com/repos/" + project(repo, platform) + "/git/trees/" +
                       quote(repo.get("default_branch") or "main", safe=""), {"recursive": "1"})
        if not isinstance(tree, dict) or tree.get("truncated"):
            return []
        return [x["path"] for x in tree.get("tree", [])[:6000] if x.get("type") == "blob" and isinstance(x.get("path"), str)]
    tree = request("https://gitlab.com/api/v4/projects/" + str(repo["id"]) + "/repository/tree",
                   {"per_page": 100, "recursive": "true"}, gitlab=True) or []
    return [x["path"] for x in tree if x.get("type") == "blob" and isinstance(x.get("path"), str)]


def raw_url(repo, platform, file):
    branch = quote(repo.get("default_branch") or "main", safe="/")
    path = "/".join(quote(part, safe="") for part in file.split("/"))
    full = "/".join(quote(part, safe="") for part in project(repo, platform).split("/"))
    if platform == "github":
        return "https://raw.githubusercontent.com/" + full + "/" + branch + "/" + path
    return "https://gitlab.com/" + full + "/-/raw/" + branch + "/" + path


def media(repo, platform, files):
    icons = [p for p in files if p.lower().endswith("sce_sys/icon0.png")]
    if not icons:
        icons = [p for p in files if p.lower() in ("icon0.png", "assets/icon0.png", "images/icon0.png", "icon.png")]
    images = [p for p in files if p.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
              and re.search(r"(^|/)(screenshots?|captures?)/|screenshot[_0-9-]", p, re.I)]
    return (raw_url(repo, platform, min(icons, key=len)) if icons else "",
            [raw_url(repo, platform, p) for p in sorted(images)[:5]])


def find_vpk(repo, platform):
    if platform == "github":
        releases = request("https://api.github.com/repos/" + project(repo, platform) + "/releases", {"per_page": 5}) or []
        for rel in releases:
            if rel.get("draft"):
                continue
            assets = [a for a in rel.get("assets", []) if str(a.get("name", "")).lower().endswith(".vpk")]
            if len(assets) == 1 and str(assets[0].get("browser_download_url", "")).startswith("https://github.com/"):
                return {"url": assets[0]["browser_download_url"], "size": int(assets[0].get("size") or 0),
                        "version": str(rel.get("tag_name") or "")[:60], "date": str(rel.get("published_at") or "")[:10]}
    return {}


def analyze(repo, platform, known, authors):
    full = project(repo, platform)
    if platform + ":" + full.lower() in known:
        return "catalogued", None
    text = readme(repo, platform)
    metadata = str(repo.get("name") or "") + " " + str(repo.get("description") or "")
    if not re.search(r"ps[\s-]?vita|vitasdk|vita2d|vita[-_]?homebrew|vita", metadata + " " + text[:12000], re.I):
        return "unrelated", None
    files = files_for(repo, platform)
    vpk = find_vpk(repo, platform)
    score, reasons = 0, []
    if re.search(r"ps[\s-]?vita|vitasdk|vita[-_]?homebrew", metadata, re.I):
        score += 4
        reasons.append("Metadatos PS Vita")
    if re.search(r"ps[\s-]?vita|vitasdk|vita2d", text, re.I):
        score += 3
        reasons.append("README PS Vita/VitaSDK")
    if "vita_create_vpk" in text or "vita_create_self" in text:
        score += 5
        reasons.append("Configuración VitaSDK")
    if any(p.lower().endswith("sce_sys/icon0.png") for p in files):
        score += 4
        reasons.append("Icono de PS Vita")
    if vpk:
        score += 7
        reasons.append("VPK publicado")
    if score < 4:
        return "uncertain", None
    icon, screenshots = media(repo, platform, files)
    desc = short_text(repo.get("description"), 180)
    paragraph = readme_paragraph(text)
    if not desc:
        desc = short_text(paragraph, 180)
    long_desc = paragraph if paragraph != desc else ""
    owner = repo.get("owner") if platform == "github" else repo.get("namespace")
    owner = owner if isinstance(owner, dict) else {}
    handle = str(owner.get("login") or owner.get("path") or full.split("/")[0])
    profile_url = "https://" + ("github.com/" if platform == "github" else "gitlab.com/") + handle
    profile_key = platform + ":" + handle.lower()
    ids = [x for x in authors.get(profile_key, []) if x]
    app_url = "https://" + ("github.com/" if platform == "github" else "gitlab.com/") + full
    links = [{"type": "Repository", "name": "Repositorio original", "url": app_url}]
    if vpk:
        links.append({"type": "Download", "name": "VPK (revisión pendiente)", "url": vpk["url"]})
    app = {
        "id": re.sub(r"[^a-z0-9]+", "-", str(repo.get("name") or "").lower()).strip("-")[:75] or "discovery-" + str(repo["id"]),
        "title_id": "", "name": str(repo.get("name") or "")[:120],
        "description": desc, "long_description": long_desc, "author_ids": ids,
        "category_id": "", "subcategory_ids": [], "version": vpk.get("version", ""),
        "version_date": vpk.get("date", ""), "requirements": "", "size": vpk.get("size", 0),
        "status": "", "icon": icon, "screenshots": screenshots, "links": links}
    author_draft = None if ids else {
        "id": re.sub(r"[^a-z0-9]+", "-", handle.lower()).strip("-"),
        "name": handle, "avatar": owner.get("avatar_url") or "", "bio": "",
        "links": [{"type": platform.title(), "name": platform.title(), "url": profile_url}]}
    entry = {"key": repo_id(repo, platform), "platform": platform, "project": full,
             "source_url": app_url, "author_profile": profile_url,
             "author_draft": author_draft, "discovered_at": utc(), "review_state": "new",
             "score": score, "evidence": reasons, "app_draft": app,
             "missing_fields": [k for k in ("title_id", "author_ids", "category_id", "version",
                                             "version_date", "size", "status", "icon")
                                if not app[k]]}
    return "candidate", entry


def run():
    catalog, authors = to_v1(request(CATALOG_URL)), to_v1(request(AUTHORS_URL))
    if not isinstance(catalog, (dict, list)) or not isinstance(authors, list):
        raise RuntimeError("Invalid official catalog response; previous discovery data retained")
    known = official_repo_keys(catalog)
    profiles, existing = profiles_from_authors(authors)
    state = read_json(STATE, {"schema_version": 1, "seen": {}, "queue": [], "profiles": {},
                              "profile_cursor": 0, "query_index": 0, "query_page": 1, "run_count": 0})
    candidates = read_json(CANDIDATES, {})
    if not isinstance(candidates, dict) or not isinstance(state.get("queue"), list):
        raise RuntimeError("Corrupt discovery data; refusing overwrite")
    failures = []
    globally_found = scanned_profiles = checked = found = 0
    try:
        globally_found = global_search(state)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        failures.append("Global search: " + str(exc)[:150])
    try:
        globally_found += global_gitlab_search(state)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        failures.append("GitLab search: " + str(exc)[:150])
    try:
        scanned_profiles = scan_profiles(state, profiles)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        failures.append("Author profiles: " + str(exc)[:150])
    for _ in range(min(REPO_LIMIT, len(state["queue"]))):
        task = state["queue"].pop(0)
        key = task["key"]
        if key in state["seen"]:
            continue
        try:
            status, entry = analyze(task["repo"], task["platform"], known, existing)
        except (RuntimeError, ValueError, KeyError, requests.RequestException) as exc:
            state["queue"].append(task)
            failures.append(key + ": " + str(exc)[:150])
            if "limit" in str(exc).lower():
                break
            continue
        state["seen"][key] = {"result": status, "checked_at": utc(), "project": project(task["repo"], task["platform"])}
        checked += 1
        if entry:
            candidates[key] = entry
            found += 1
    state["run_count"] += 1
    state["last_run"] = utc()
    state["errors"] = failures[:10]
    state["last_round"] = {"global_results": globally_found, "profiles_checked": scanned_profiles,
                           "repositories_checked": checked, "new_candidates": found, "pending": len(state["queue"])}
    # Migrate prior queue snapshots to compact state, including initial runs.
    for task in state["queue"]:
        task["repo"] = compact_repo(task["repo"], task["platform"])
    write_json(STATE, state)
    write_json(CANDIDATES, candidates)
    for key, item in candidates.items():
        slug = key.replace(":", "_")
        if re.fullmatch(r"(gh|gl)_[0-9]+", slug):
            write_json(DRAFTS / (slug + ".json"), item["app_draft"])
    print("Discovery:", json.dumps(state["last_round"]))
    if failures:
        print("Deferred errors:", failures[:5])
    return state, candidates


if __name__ == "__main__":
    run()
