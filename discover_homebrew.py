#!/usr/bin/env python3
"""Incremental PS Vita homebrew discovery. Produces review-only JSON drafts."""
import base64
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests
from catalog_schema_v2_compat import to_v1

BASE = Path("data/discovery")
STATE = BASE / "state.json"
CANDIDATES = BASE / "candidates.json"
DRAFTS = Path("docs/discovery-drafts")
CATALOG_URL = os.getenv("PSVITAALIVE_CATALOG_URL", "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json")
AUTHORS_URL = os.getenv("PSVITAALIVE_AUTHORS_URL", "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/authors.json")
PROFILE_LIMIT = max(1, min(8, int(os.getenv("DISCOVERY_PROFILE_LIMIT", "3"))))
MEDIA_SCAN_VERSION = 2
LINEAGE_SCAN_VERSION = 1
LINEAGE_REFRESH_LIMIT = max(0, min(150, int(os.getenv("DISCOVERY_LINEAGE_REFRESH_LIMIT", "90"))))
LINEAGE_REQUEST_CAP = max(60, min(320, int(os.getenv("DISCOVERY_LINEAGE_REQUEST_CAP", "200"))))
# Recheck older discoveries gradually without exhausting the discovery quota.
MEDIA_REFRESH_LIMIT = max(0, min(100, int(os.getenv("DISCOVERY_MEDIA_REFRESH_LIMIT", "60"))))
MEDIA_REFRESH_REQUEST_CAP = max(40, min(260, int(os.getenv("DISCOVERY_MEDIA_REQUEST_CAP", "185"))))
# Up to 150 repository checks per round (previously only 18).
REPO_LIMIT = max(1, min(200, int(os.getenv("DISCOVERY_REPO_LIMIT", "150"))))
# API and wall-clock safeguards: GitHub Actions GITHUB_TOKEN allows ~1000 API calls/hour.
MAX_REQUESTS = max(80, min(750, int(os.getenv("DISCOVERY_REQUEST_BUDGET", "520"))))
MAX_SECONDS = max(120, min(1080, int(os.getenv("DISCOVERY_TIME_BUDGET", "720"))))
REQUEST_COUNT = 0
LOW_RATE_LIMIT = False
START_TIME = time.monotonic()
HTTP = requests.Session()
class DiscoveryBudgetReached(RuntimeError):
    """The next batch can resume from the durable checkpoint."""
    pass

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
    global REQUEST_COUNT, LOW_RATE_LIMIT
    if (REQUEST_COUNT >= MAX_REQUESTS or LOW_RATE_LIMIT or
            time.monotonic() - START_TIME >= MAX_SECONDS):
        raise DiscoveryBudgetReached("Límite prudente de tiempo o peticiones; continuamos en la próxima ronda")
    headers = GL_HEADERS if gitlab else HEADERS
    response = HTTP.get(url, params=params, headers=headers, timeout=(5, 18))
    REQUEST_COUNT += 1
    if response.status_code == 404:
        return None
    # GitHub tree API returns 409 for empty/unborn branches. No files to inspect,
    # but a release or README may still confirm a Vita project.
    if response.status_code == 409 and "/git/trees/" in url:
        return {"tree": []}
    if response.status_code in (403, 429):
        raise DiscoveryBudgetReached("API rate limit: " + (urlparse(url).hostname or ""))
    response.raise_for_status()
    # Search uses a separate quota; protect the main authenticated GitHub REST quota.
    if url.startswith("https://api.github.com/") and "/search/" not in url:
        remaining = response.headers.get("X-RateLimit-Remaining")
        try:
            if remaining is not None and int(remaining) < 70:
                LOW_RATE_LIMIT = True
        except ValueError:
            pass
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


def official_apps(catalog):
    """The public PSVitaAlive catalog can be a list or a list in a wrapper."""
    if isinstance(catalog, list):
        return [x for x in catalog if isinstance(x, dict)]
    if isinstance(catalog, dict):
        for key in ("apps", "items", "catalog"):
            if isinstance(catalog.get(key), list):
                return [x for x in catalog[key] if isinstance(x, dict)]
    raise ValueError("Catálogo oficial sin una lista válida de aplicaciones")


def norm_title(value):
    """Match only precise normalized titles; never infer identity from vague text."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def official_app_index(catalog):
    apps = official_apps(catalog)
    titles, names_and_authors, normalized_titles = set(), set(), set()
    for app in apps:
        tid = str(app.get("title_id") or "").strip().upper()
        if tid:
            titles.add(tid)
        names = {norm_title(app.get("name")), norm_title(app.get("id"))}
        normalized_titles.update(names - {""})
        author_ids = app.get("author_ids") or ([app.get("author_id")] if app.get("author_id") else [])
        if isinstance(author_ids, str):
            author_ids = [author_ids]
        for name in names - {""}:
            for aid in author_ids:
                if aid:
                    names_and_authors.add((name, str(aid).casefold()))
    return {"repos": official_repo_keys(catalog), "title_ids": titles,
            "name_authors": names_and_authors, "normalized_titles": normalized_titles,
            "app_count": len(apps)}


def repository_lineage_from_api(repo, platform):
    """Only GitHub/GitLab API ancestry proves that a repo is a fork.

    A similarly named but independently developed homebrew is NOT a duplicate.
    """
    if not isinstance(repo, dict):
        return {"lineage_parent": "", "lineage_source": "", "lineage_is_fork": False}
    if platform == "github":
        source = repo.get("source") if isinstance(repo.get("source"), dict) else {}
        parent = repo.get("parent") if isinstance(repo.get("parent"), dict) else {}
        # GitHub's repository detail returns both source (root) and parent.
        root = str(source.get("full_name") or "").strip()
        previous = str(parent.get("full_name") or "").strip()
        fork = repo.get("fork") is True
        return {"lineage_source": "github:" + root.casefold() if fork and root else "",
                "lineage_parent": "github:" + previous.casefold() if fork and previous else "",
                "lineage_is_fork": fork}
    if platform == "gitlab":
        parent = repo.get("forked_from_project") if isinstance(
            repo.get("forked_from_project"), dict) else {}
        full = str(parent.get("path_with_namespace") or "").strip()
        return {"lineage_source": "", "lineage_parent": "gitlab:" + full.casefold() if full else "",
                "lineage_is_fork": bool(full)}
    return {"lineage_parent": "", "lineage_source": "", "lineage_is_fork": False}


def fetch_repository_lineage(project_name, platform, compact=None):
    """Retrieve reliable fork ancestry using one repository metadata request."""
    if platform not in ("github", "gitlab") or "/" not in str(project_name or ""):
        return None
    if isinstance(compact, dict) and compact.get("fork") is False:
        # Explicit non-fork status from GitHub's own search/user API is enough.
        return {"lineage_parent": "", "lineage_source": "",
                "lineage_is_fork": False, "lineage_check_version": LINEAGE_SCAN_VERSION}
    if platform == "github":
        url = "https://api.github.com/repos/" + "/".join(
            quote(part, safe="") for part in project_name.split("/"))
    else:
        # GitLab project path must be URL-encoded as a single path parameter.
        url = "https://gitlab.com/api/v4/projects/" + quote(project_name, safe="")
    detail = request(url, gitlab=platform == "gitlab")
    if not isinstance(detail, dict):
        return None
    return {**repository_lineage_from_api(detail, platform),
            "lineage_check_version": LINEAGE_SCAN_VERSION}


def enrich_existing_lineage(candidates, official):
    """Check existing discoveries a bounded amount each run.

    Prioritize same-name repos to remove proven duplicates early. Remaining
    candidates are still checked incrementally to detect renamed forks.
    """
    github_basenames = {norm_title(x.rsplit("/", 1)[-1]) for x in official["repos"]
                        if x.startswith("github:")}
    catalog_titles = official["normalized_titles"]
    needs_check = [x for x in candidates.values()
                   if isinstance(x, dict) and x.get("lineage_check_version") != LINEAGE_SCAN_VERSION]
    def priority(entry):
        app = entry.get("app_draft") or {}
        repo_name = str(entry.get("project") or "").rsplit("/", 1)[-1]
        aliases = {norm_title(repo_name), norm_title(app.get("name")), norm_title(app.get("id"))}
        return (0 if (aliases & github_basenames or aliases & catalog_titles) else 1,
                str(entry.get("discovered_at") or ""))
    needs_check.sort(key=priority)
    checked = 0
    requests_before = REQUEST_COUNT
    for entry in needs_check:
        if checked >= LINEAGE_REFRESH_LIMIT or REQUEST_COUNT - requests_before >= LINEAGE_REQUEST_CAP:
            break
        if LOW_RATE_LIMIT or REQUEST_COUNT >= MAX_REQUESTS - 160 or time.monotonic() - START_TIME >= MAX_SECONDS - 120:
            break
        try:
            info = fetch_repository_lineage(entry.get("project"), entry.get("platform"))
            if info is None:
                continue
            entry.update(info)
            entry["lineage_checked_at"] = utc()
            checked += 1
        except DiscoveryBudgetReached:
            break
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError):
            continue
    return {"checked": checked, "remaining": max(0, len(needs_check) - checked),
            "http_requests": REQUEST_COUNT - requests_before}


def match_official_candidate(entry, official):
    """Return the reliable matching evidence or None (no name-only guesses)."""
    if not isinstance(entry, dict):
        return None
    app = entry.get("app_draft") or {}
    links = app.get("links") or []
    # Prefer verified canonical repositories, including GitHub release links.
    for link in [entry.get("source_url"), *(
            x.get("url") for x in links if isinstance(x, dict))]:
        key = project_url_key(link)
        if key and key in official["repos"]:
            return "repo:" + key
    # Fork ancestry verified by the hosting provider is stronger than names.
    # A different author or empty title_id must not make a fork look new.
    if entry.get("lineage_is_fork"):
        for field in ("lineage_source", "lineage_parent"):
            origin = str(entry.get(field) or "").casefold()
            if origin and origin in official["repos"]:
                return "fork_of:" + origin
    tid = str(app.get("title_id") or "").strip().upper()
    if tid and tid in official["title_ids"]:
        return "title_id:" + tid
    author_ids = app.get("author_ids") or []
    if isinstance(author_ids, str):
        author_ids = [author_ids]
    names = {norm_title(app.get("name")), norm_title(app.get("id"))} - {""}
    if names and any((name, str(aid).casefold()) in official["name_authors"]
                     for name in names for aid in author_ids if aid):
        return "same_author_and_title"
    return None


def reconcile_catalog(state, candidates, official):
    """Hide newly catalogued discoveries on EVERY run, not only when first found.

    Candidate data and public drafts disappear; state retains evidence of the match.
    This never edits catalog.json or any PSVitaAlive canonical source JSON.
    """
    count = 0
    history = state.setdefault("catalogued_discoveries", {})
    for key, entry in list(candidates.items()):
        reason = match_official_candidate(entry, official)
        if not reason:
            continue
        history[key] = {"project": entry.get("project"), "matched_on": reason,
                        "catalogued_at": utc()}
        state.setdefault("seen", {}).setdefault(key, {})["result"] = "catalogued"
        state["seen"][key]["catalogue_match"] = reason
        state.get("target_filter_audit", {}).pop(key, None)
        del candidates[key]
        count += 1
    # Previously queued registered repositories do not consume the scan budget.
    registered = official["repos"]
    remaining = []
    for task in state.get("queue", []):
        repo = task.get("repo") or {}
        key = task.get("platform", "") + ":" + project(repo, task.get("platform"))
        if key.casefold() not in registered:
            remaining.append(task)
            continue
        state.setdefault("seen", {})[task["key"]] = {
            "result": "catalogued", "checked_at": utc(),
            "project": project(repo, task.get("platform")),
        }
    state["queue"] = remaining
    return count


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
              "default_branch", "topics", "archived", "disabled", "fork", "needs_refresh")
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
    # GitLab project search on "ps vita" matches unrelated occurrences of "vita".
    # Use platform-specific words; existing author scanning catches less obvious names.
    terms = ("psvita", "vitasdk", "vita-homebrew", "playstation-vita")
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
    """Fetch nested repository files. GitLab trees require explicit pagination."""
    if platform == "github":
        tree = request("https://api.github.com/repos/" + project(repo, platform) + "/git/trees/" +
                       quote(repo.get("default_branch") or "main", safe=""), {"recursive": "1"})
        if not isinstance(tree, dict):
            return []
        # Even truncated GitHub trees contain useful file paths: do not discard
        # their entire partial result. Stop at a bounded 16000 entries.
        return [x["path"] for x in tree.get("tree", [])[:16000]
                if x.get("type") == "blob" and isinstance(x.get("path"), str)]
    paths = []
    pid = quote(str(repo["id"]), safe="")
    for page in range(1, 16):  # Up to 1,500 entries, with API budget guard.
        tree = request("https://gitlab.com/api/v4/projects/" + pid + "/repository/tree",
                       {"per_page": 100, "page": page, "recursive": "true",
                        "ref": repo.get("default_branch") or "main"}, gitlab=True) or []
        if not isinstance(tree, list):
            break
        paths.extend(x["path"] for x in tree
                     if x.get("type") == "blob" and isinstance(x.get("path"), str))
        if len(tree) < 100:
            break
    return paths


def raw_url(repo, platform, file):
    branch = quote(repo.get("default_branch") or "main", safe="/")
    path = "/".join(quote(part, safe="") for part in file.split("/"))
    full = "/".join(quote(part, safe="") for part in project(repo, platform).split("/"))
    if platform == "github":
        return "https://raw.githubusercontent.com/" + full + "/" + branch + "/" + path
    return "https://gitlab.com/" + full + "/-/raw/" + branch + "/" + path


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
IMAGE_DIRS = frozenset(("screenshots", "screenshot", "screens", "screen", "captures",
                       "capture", "shots", "shot", "gallery", "previews", "preview"))
IMAGE_NAME = re.compile(
    r"(?:screenshot|screen[_ -]?\d|capture[_ -]?\d|preview[_ -]?\d?|"
    r"gameplay|in[_ -]?game|title[_ -]?screen)", re.I)
UNSUITABLE_IMAGE = re.compile(
    r"(?:^|[/_.-])(?:badge|button|sprite|tileset|texture|font|flag|avatar|logo|"
    r"icon|thumbnail|banner|button|cursor|loading|spinner|startup)(?:[/_.-]|$)", re.I)


def readme_image_paths(text, files):
    """Collect only README-embedded images that actually exist in the file tree."""
    if not text or not files:
        return set()
    available = {p.casefold(): p for p in files}
    references = re.findall(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)", text)
    references += re.findall(r"<img\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", text, re.I)
    found = set()
    for src in references[:150]:
        if src.startswith(("http:", "https:", "//", "data:")):
            continue
        path = unquote(src.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/"):
            continue
        while path.startswith("./"):
            path = path[2:]
        resolved = available.get(path.casefold())
        if resolved and resolved.lower().endswith(IMAGE_EXTENSIONS):
            found.add(resolved)
    return found


def media(repo, platform, files, readme_text=""):
    """Conservatively rank actual images, including deeply nested sce_sys paths.

    The fallback pic0 / LiveArea background becomes a screenshot only when no
    real screenshots were found. No URL is guessed without a matching blob path.
    """
    files = sorted({p for p in files if isinstance(p, str) and
                    p.lower().endswith(IMAGE_EXTENSIONS)}, key=lambda p: (p.casefold(), p))
    embedded = readme_image_paths(readme_text, files)
    icons = []
    screenshots = []
    fallback = []
    for path in files:
        parts = path.lower().split("/")
        filename = parts[-1]
        folders = parts[:-1]
        nested = "/".join(parts)
        livearea = "sce_sys" in folders and "livearea" in folders and "contents" in folders
        pic0 = filename == "pic0.png" and ("sce_sys" in folders or len(parts) == 1)
        if pic0:
            fallback.append((0, len(parts), path))
            continue
        if livearea and filename in ("bg.png", "bg0.png", "background.png"):
            fallback.append((1, len(parts), path))
            continue

        if filename == "icon0.png":
            # Native VPK icon (even at project/src/sce_sys/icon0.png) wins.
            rank = 0 if "sce_sys" in folders else 2
            icons.append((rank, len(parts), path))
            continue
        if filename in ("icon.png", "appicon.png", "app_icon.png", "vita_icon.png",
                        "bubble.png", "bubble_icon.png"):
            icons.append((3, len(parts), path))
            continue
        if re.fullmatch(r"(?:app[_-]?)?icon[_-]?\d{2,4}\.png", filename):
            icons.append((4, len(parts), path))
            continue

        # Ignore art/assets often confused with screenshots.
        if UNSUITABLE_IMAGE.search(nested):
            continue
        by_folder = any(folder in IMAGE_DIRS for folder in folders)
        by_name = bool(IMAGE_NAME.search(filename))
        if by_folder or by_name or path in embedded:
            rank = 0 if by_folder else 1 if by_name else 2
            screenshots.append((rank, len(parts), path))

    icon = min(icons, default=None)
    best_shots = [p for _, _, p in sorted(screenshots, key=lambda t: (t[0], t[1], t[2].casefold()))[:5]]
    if not best_shots and fallback:
        best_shots = [min(fallback, key=lambda t: (t[0], t[1], t[2].casefold()))[2]]
    return (raw_url(repo, platform, icon[2]) if icon else "",
            [raw_url(repo, platform, p) for p in best_shots])


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



# Versioned, conservative destination classification. "Mentions Vita" is not enough.
TARGET_FILTER_VERSION = 4
VITA_PATTERN = r"(?:ps[\s_-]*vita|playstation\s+vita|ps[\s_-]*tv|pstv)"
TARGET_PATTERN = re.compile(
    r"(?:\b(?:for|to|on|runs?\s+on|installed?\s+on|built\s+for|made\s+for|"
    r"port(?:ed)?\s+to|compatible\s+with|para|sur|für)\s+(?:the\s+|a\s+)?"
    + VITA_PATTERN
    + r"|\b" + VITA_PATTERN + r"\s+(?:homebrew|app(?:lication)?|client|game|plugin|port|patch|mod|version|release|tool|reader|player)\b)",
    re.I,
)
SWITCH_DESTINATION = re.compile(
    r"(?:\b(?:recompil\w*|port(?:ed)?|compil\w*|build\w*|target\w*)"
    r".{0,90}\b(?:for|to|para|on)\s+(?:the\s+)?(?:nintendo\s+)?switch\b"
    r"|\b(?:nintendo\s+switch|switch)\s+homebrew\b"
    r"|\b(?:libnx|devkitpro\s+switch|switch\s+arm64)\b)",
    re.I,
)
VITA_EMULATOR = re.compile(
    r"(?:\b(?:ps[\s_-]*vita|playstation\s+vita)\s+emulator\b"
    r"|\bemulat(?:or|ion)\s+of\s+(?:the\s+)?(?:ps[\s_-]*vita|playstation\s+vita)\b)", re.I,
)
BUILD_VITA = re.compile(r"\bvita_create_(?:vpk|self)\s*\(", re.I)
PATCH = re.compile(r"\b(?:patch(?:es|ing)?|translation|localization|mod|traducci[oó]n|tradu[cç][aã]o|parche)\b", re.I)


def classify_target(name, description, readme_text, paths=(), vpk=None):
    """Returns (status, kind, reason). Never treat a mere PS Vita mention as eligibility."""
    meta = (str(name or "") + " " + str(description or "")).strip()
    body = str(readme_text or "")[:16000]
    combined = meta + "\n" + body
    lower_name = str(name or "").lower()
    has_vpk = bool(vpk)
    build = bool(BUILD_VITA.search(body))
    vita_icon = any(str(p).lower().endswith("sce_sys/icon0.png") for p in paths)
    vita_plugin = any(str(p).lower().endswith((".skprx", ".suprx")) for p in paths)
    # A mention of a patch database is not itself a patch project.
    patch = bool(PATCH.search(str(name or "")) or re.search(
        r"\b(?:patch|translation|mod)\b(?:\s+\w+){0,6}\s+"
        r"(?:for|to|on|para)\s+(?:a\s+|the\s+)?"
        r"(?:ps[\s_-]*vita|playstation\s+vita)"
        r"|(?:ps[\s_-]*vita|playstation\s+vita)\s+"
        r"(?:game\s+|title\s+|english\s+|translation\s+|texture\s+)?"
        r"(?:patch|translation|mod)\b",
        meta, re.I))
    kind = "patch" if patch else "homebrew"

    # Explicit outputs for another platform override incidental Vita references.
    if lower_name == "vita3k" or (VITA_EMULATOR.search(meta) and not has_vpk):
        return "excluded", kind, "Emulador de PS Vita para otras plataformas"
    if (SWITCH_DESTINATION.search(meta) or
            (SWITCH_DESTINATION.search(body[:1800]) and not has_vpk)):
        if not has_vpk and not build:
            return "excluded", kind, "El destino es Nintendo Switch, no PS Vita"
    if re.search(r"(?:ps[\s_-]*vita|playstation\s+vita).{0,100}\b(?:to|for|para)\s+(?:the\s+)?(?:nintendo\s+)?switch\b",
                 combined[:2500], re.I) and not has_vpk:
        return "excluded", kind, "Proyecto que parte de Vita pero se dirige a Switch"

    # Related PC management tools should not be misidentified as console apps.
    desktop = bool(re.search(
        r"\b(?:windows\s+(?:desktop|app|program)|desktop\s+(?:app|utility|tool)|"
        r"pc[-\s]+only|for\s+(?:windows|macos|linux)\b|native\s+(?:rust|python)\s+(?:builder|tool))\b",
        meta, re.I))
    if desktop and not has_vpk and not build and not vita_icon:
        return "review", kind, "Herramienta de PC relacionada con PS Vita"

    # Keep PSP-only patches for separate review, not as PS Vita game patches.
    if patch and re.search(r"\b(?:PSP|PPSSPP|playstation\s+portable)\b", meta, re.I) and not re.search(
            r"(?:ps[\s_-]*vita|playstation\s+vita)\s+(?:game|patch|title)", meta, re.I):
        return "review", kind, "Parche PSP compatible con Vita; requiere revisión"

    targeted = bool(TARGET_PATTERN.search(meta) or TARGET_PATTERN.search(body[:4000]))
    targeted = targeted or bool(re.search(
        r"(?:ps[\s_-]*vita|playstation\s+vita)\s+(?:port|build)\s+of\b", combined, re.I))
    # Kernel plugins often ship only .skprx sources or a taiHEN config (no VPK).
    vita_kernel_plugin = bool(re.search(
        r"(?:ps[\s_-]*vita|playstation\s+vita).{0,24}\b(?:kernel\s+)?plugin\b"
        r"|\b(?:kernel\s+)?plugin\b.{0,32}(?:ps[\s_-]*vita|playstation\s+vita)",
        meta + "\n" + body[:1400], re.I)) and bool(re.search(
            r"taihen|henkaku|ens[oō]|\*KERNEL|ur0:tai|ksce[A-Z]|\.skprx", combined, re.I))
    if has_vpk:
        return "candidate", kind, "VPK en release público"
    if build and (targeted or vita_icon or vita_plugin):
        return "candidate", kind, "Compilación VitaSDK con destino PS Vita"
    if targeted and (vita_icon or vita_plugin):
        return "candidate", kind, "Destino Vita confirmado por recursos nativos"
    if vita_kernel_plugin:
        return "candidate", "plugin", "Plugin para el kernel de PS Vita y taiHEN"
    if patch and targeted and not re.search(r"\b(?:nintendo\s+switch|libnx)\b", combined[:1800], re.I):
        return "candidate", kind, "Posible parche para PS Vita (revisión necesaria)"
    return "review", kind, "No está confirmado que se ejecute o aplique en PS Vita"


def reclassify_existing(state, candidates):
    """Clean old candidates once without re-fetching the same repositories."""
    audit = state.setdefault("target_filter_audit", {})
    # Restored candidates are no longer held for review; never keep stale notes.
    for key in candidates:
        audit.pop(key, None)
    if int(state.get("target_filter_version", 0)) >= TARGET_FILTER_VERSION:
        return 0
    state.setdefault("queue", [])
    count = 0
    for key, entry in list(candidates.items()):
        app = entry.get("app_draft") or {}
        links = app.get("links") or []
        vpk = next((link for link in links if isinstance(link, dict) and
                    link.get("type") == "Download" and
                    str(link.get("url") or "").lower().endswith(".vpk")), None)
        paths = [str(app.get("icon") or "")]
        result, kind, reason = classify_target(
            app.get("name"), app.get("description"), app.get("long_description"), paths, vpk)
        if result != "candidate":
            audit[key] = {"result": result, "reason": reason,
                          "project": entry.get("project"), "filtered_at": utc()}
            if key in state.get("seen", {}):
                state["seen"][key]["result"] = result
                state["seen"][key]["reason"] = reason
            del candidates[key]
            count += 1
        else:
            entry["project_type"] = kind
            entry["target_reason"] = reason
    # Previously held cases deserve one rescan with the improved Vita-specific
    # rules; do not rescan rejected Switch/Vita emulator projects.
    if int(state.get("target_review_requeued_version", 0)) < TARGET_FILTER_VERSION:
        tasks = []
        for key, data in audit.items():
            if data.get("result") != "review" or not re.fullmatch(r"(gh|gl):[0-9]+", key):
                continue
            platform = "github" if key.startswith("gh:") else "gitlab"
            full = data.get("project") or ""
            if not full:
                continue
            name = full.rsplit("/", 1)[-1]
            repo = {"id": int(key.split(":")[1]), "name": name, "needs_refresh": True,
                    "default_branch": "main",
                    ("full_name" if platform == "github" else "path_with_namespace"): full}
            if key in state.get("seen", {}):
                del state["seen"][key]
            tasks.append({"key": key, "platform": platform,
                          "repo": compact_repo(repo, platform)})
        task_keys = {task["key"] for task in tasks}
        state["queue"] = tasks + [task for task in state["queue"] if task["key"] not in task_keys]
        state["target_review_requeued_version"] = TARGET_FILTER_VERSION
    state["target_filter_version"] = TARGET_FILTER_VERSION
    return count


def media_image_fallback(url):
    path = urlparse(url or "").path.casefold()
    return path.endswith(("/pic0.png", "/livearea/contents/bg.png",
                          "/livearea/contents/bg0.png", "/livearea/contents/background.png"))


def enrich_existing_media(candidates):
    """Revisit historic discoveries in bounded batches; retain all previous data.

    Rechecks each candidate once after this algorithm changes, and missing-media
    projects can be retried after 30 days. No changes to canonical PSVitaAlive.
    """
    refreshed = new_icons = added_screens = changed = 0
    request_cap = min(MAX_REQUESTS - 120, MEDIA_REFRESH_REQUEST_CAP)
    def eligible(entry):
        last_ver = int(entry.get("media_scan_version") or 0)
        if last_ver < MEDIA_SCAN_VERSION:
            return True
        app = entry.get("app_draft") or {}
        return (not app.get("icon") or not app.get("screenshots")) and due(
            entry.get("media_checked_at"), days=30)
    # Empty-media discoveries first, followed by those with only one media type.
    items = sorted(
        (entry for entry in candidates.values() if isinstance(entry, dict) and eligible(entry)),
        key=lambda e: (bool((e.get("app_draft") or {}).get("icon")) +
                       bool((e.get("app_draft") or {}).get("screenshots")),
                       e.get("discovered_at") or ""),
    )
    for entry in items:
        if refreshed >= MEDIA_REFRESH_LIMIT or REQUEST_COUNT >= request_cap or LOW_RATE_LIMIT:
            break
        if time.monotonic() - START_TIME >= MAX_SECONDS - 120:
            break
        platform = entry.get("platform")
        project_name = str(entry.get("project") or "")
        if platform not in ("github", "gitlab") or "/" not in project_name:
            # Preserve uncertain historical records; never fabricate a URL.
            entry["media_scan_version"] = MEDIA_SCAN_VERSION
            entry["media_checked_at"] = utc()
            refreshed += 1
            continue
        try:
            if platform == "github":
                url = "https://api.github.com/repos/" + "/".join(
                    quote(part, safe="") for part in project_name.split("/"))
            else:
                url = "https://gitlab.com/api/v4/projects/" + quote(
                    entry["key"].split(":", 1)[1], safe="")
            repo = request(url, gitlab=platform == "gitlab")
            if repo:
                paths = files_for(repo, platform)
                icon, screenshots = media(repo, platform, paths)
                # A README often describes images under docs/images/01.png.
                # Check it only when no real screenshots exist.
                if paths and (not screenshots or all(
                        media_image_fallback(p) for p in screenshots)):
                    text = readme(repo, platform)
                    if text:
                        icon, screenshots = media(repo, platform, paths, text)
                app = entry.get("app_draft") or {}
                previous_icon = str(app.get("icon") or "")
                previous_screens = [x for x in (app.get("screenshots") or [])
                                    if isinstance(x, str) and x]
                if icon and icon != previous_icon:
                    app["icon"] = icon
                    changed += 1
                    if not previous_icon:
                        new_icons += 1
                if screenshots:
                    # Never let a background/pic0 replace real existing captures.
                    if (len(screenshots) == 1 and media_image_fallback(screenshots[0])
                            and previous_screens):
                        screenshots = []
                    merged = list(dict.fromkeys(screenshots + previous_screens))[:5]
                    if merged != previous_screens:
                        added_screens += len([x for x in merged if x not in previous_screens])
                        app["screenshots"] = merged
                        changed += 1
                if app.get("icon"):
                    entry["missing_fields"] = [k for k in entry.get("missing_fields", [])
                                               if k != "icon"]
            entry["media_scan_version"] = MEDIA_SCAN_VERSION
            entry["media_checked_at"] = utc()
            refreshed += 1
        except DiscoveryBudgetReached:
            break
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
            # Transient errors are eligible to retry on the next scheduled run.
            print("Media deferred:", project_name, str(exc)[:120])
            continue
    pending = sum(1 for entry in candidates.values()
                  if isinstance(entry, dict) and eligible(entry))
    return {"refreshed": refreshed, "icons_added": new_icons,
            "screenshots_added": added_screens, "fields_updated": changed,
            "still_to_refresh": pending}


def analyze(repo, platform, known, authors):
    if repo.get("needs_refresh"):
        if platform == "github":
            repo = request("https://api.github.com/repos/" + project(repo, platform)) or repo
        else:
            repo = request("https://gitlab.com/api/v4/projects/" + str(repo["id"]),
                           gitlab=True) or repo
    full = project(repo, platform)
    if platform + ":" + full.lower() in known:
        return "catalogued", None
    text = readme(repo, platform)
    metadata = str(repo.get("name") or "") + " " + str(repo.get("description") or "")
    if not re.search(r"ps[\s-]?vita|vitasdk|vita2d|vita[-_]?homebrew|vita", metadata + " " + text[:12000], re.I):
        return "unrelated", None
    # Avoid expensive API calls if the description already declares a wrong target.
    early, _, early_reason = classify_target(repo.get("name"), repo.get("description"), text)
    if early == "excluded":
        return "excluded", None
    files = files_for(repo, platform)
    vpk = find_vpk(repo, platform)
    decision, kind, target_reason = classify_target(
        repo.get("name"), repo.get("description"), text, files, vpk)
    if decision != "candidate":
        return decision, None
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
    icon, screenshots = media(repo, platform, files, text)
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
             "project_type": kind, "target_reason": target_reason,
             "source_url": app_url, "author_profile": profile_url,
             "author_draft": author_draft, "discovered_at": utc(), "review_state": "new",
             "score": score, "evidence": reasons, "app_draft": app,
             "media_scan_version": MEDIA_SCAN_VERSION, "media_checked_at": utc(),
             "missing_fields": [k for k in ("title_id", "author_ids", "category_id", "version",
                                             "version_date", "size", "status", "icon")
                                if not app[k]]}
    return "candidate", entry


VITA_METADATA = re.compile(
    r"ps[\s_-]*vita|playstation[\s_-]*vita|vitasdk|vita2d|"
    r"taihen|henkaku|[.]vpk\b|vita[-_]homebrew|"
    r"(?:^|[-_/])vita(?:$|[-_/])|(?:^|[-_/])psvita(?:$|[-_/])",
    re.I,
)


def task_priority(task, known_profiles):
    """3: strong Vita metadata, 2: known author, 1: global candidate."""
    repo = task.get("repo") or {}
    platform = task.get("platform") or ""
    full = project(repo, platform)
    metadata = " ".join(str(repo.get(k) or "")
                        for k in ("name", "description", "topics")) + " " + full
    if VITA_METADATA.search(metadata):
        return 3
    owner = (repo.get("owner") or {}) if platform == "github" else (repo.get("namespace") or {})
    username = str(owner.get("login") or owner.get("path") or full.split("/")[0]).casefold()
    if platform + ":" + username in known_profiles:
        return 2
    return 1


def pop_prioritized_task(queue, profiles, examined, attempted=()):
    """Prefer Vita evidence, but never retry a failed repository in the same run."""
    if not queue:
        return None
    attempted = set(attempted)
    known = set(profiles)
    wants_low = examined % 5 == 4
    preferred = (1, 2) if wants_low else (3, 2)
    for score in preferred:
        for index, task in enumerate(queue):
            if task["key"] not in attempted and task_priority(task, known) == score:
                return queue.pop(index)
    for index, task in enumerate(queue):
        if task["key"] not in attempted:
            return queue.pop(index)
    return None


def prune_unrelated_gitlab_queue(state, profiles):
    """Discard broad GitLab hits without platform hints; preserve known authors."""
    known_authors = {name.split(":", 1)[1] for name in profiles if name.startswith("gitlab:")}
    keep = []
    removed = 0
    for task in state["queue"]:
        repo = task.get("repo") or {}
        if task.get("platform") != "gitlab":
            keep.append(task)
            continue
        project_name = project(repo, "gitlab")
        owner = project_name.split("/", 1)[0].lower()
        meta = " ".join(str(repo.get(field) or "") for field in ("name", "description", "topics"))
        meta += " " + project_name
        if owner in known_authors or re.search(
            r"ps[\s_-]*vita|playstation[\s_-]*vita|vitasdk|vita2d|"
            r"henkaku|taihen|vita[-_]homebrew|(?:^|[-_/])vita(?:$|[-_/])",
            meta, re.I):
            keep.append(task)
            continue
        state["seen"][task["key"]] = {
            "result": "irrelevant_metadata",
            "checked_at": utc(),
            "project": project_name,
            "reason": "Sin indicios de PS Vita en los metadatos del proyecto GitLab",
        }
        removed += 1
    state["queue"] = keep
    return removed


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
    official = official_app_index(catalog)
    catalogued_before = reconcile_catalog(state, candidates, official)
    # Inspect fork parents/root origins from the provider, not fuzzy names.
    lineage_refresh = enrich_existing_lineage(candidates, official)
    catalogued_before += reconcile_catalog(state, candidates, official)
    reclassified = reclassify_existing(state, candidates)
    removed_gitlab_noise = prune_unrelated_gitlab_queue(state, profiles)
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
    # Prune noisy new GitLab global results as well, before deeper requests.
    removed_gitlab_noise += prune_unrelated_gitlab_queue(state, profiles)
    try:
        scanned_profiles = scan_profiles(state, profiles)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        failures.append("Author profiles: " + str(exc)[:150])
    # Repair historical discoveries incrementally, with a separate request cap.
    media_refresh = enrich_existing_media(candidates)
    scanned = 0
    attempted_keys = set()
    for _ in range(min(REPO_LIMIT, len(state["queue"]))):
        if REQUEST_COUNT >= MAX_REQUESTS or LOW_RATE_LIMIT or time.monotonic() - START_TIME >= MAX_SECONDS:
            failures.append("Presupuesto de búsqueda alcanzado; la cola se conserva")
            break
        task = pop_prioritized_task(state["queue"], profiles, scanned, attempted_keys)
        if task is None:
            break
        key = task["key"]
        attempted_keys.add(key)
        if key in state["seen"]:
            continue
        try:
            status, entry = analyze(task["repo"], task["platform"], known, existing)
        except DiscoveryBudgetReached as exc:
            # Budget limits are normal: return this item to the FRONT of the queue.
            state["queue"].insert(0, task)
            failures.append(str(exc)[:150])
            break
        except (RuntimeError, ValueError, KeyError, requests.RequestException) as exc:
            state["queue"].append(task)
            failures.append(key + ": " + str(exc)[:150])
            continue
        state["seen"][key] = {"result": status, "checked_at": utc(), "project": project(task["repo"], task["platform"])}
        checked += 1
        scanned += 1
        if entry:
            candidates[key] = entry
            state.get("target_filter_audit", {}).pop(key, None)
            found += 1
    catalogued_after = reconcile_catalog(state, candidates, official)
    state["run_count"] += 1
    state["last_run"] = utc()
    state["errors"] = failures[:10]
    state["last_round"] = {"global_results": globally_found, "profiles_checked": scanned_profiles,
                           "repositories_checked": checked,
                           "new_candidates": found - catalogued_after,
                           "catalogued_removed": catalogued_before + catalogued_after,
                           "filtered_existing": reclassified, "http_requests": REQUEST_COUNT,
                           "request_budget": MAX_REQUESTS, "scan_limit": REPO_LIMIT,
                           "pruned_gitlab": removed_gitlab_noise, "pending": len(state["queue"]),
                           "media_refresh": media_refresh,
                           "lineage_refresh": lineage_refresh}
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
