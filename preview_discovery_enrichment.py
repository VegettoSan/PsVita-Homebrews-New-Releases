#!/usr/bin/env python3
"""Bounded, read-only pilot. Writes only an explicitly requested report file."""
from __future__ import annotations

import argparse
import base64
import json
import re
import traceback
import time
from pathlib import Path
from urllib.parse import quote

import requests
import discover_homebrew as discovery
from catalog_schema_v2_compat import to_v1
from discovery_enrichment import (
    classify_category, prioritized_paths, resolve_title, sfo_title_id, text_evidence,
)

from discovery_readiness import readiness
from discovery_author_profiles import author_proposal
from discovery_vpk import select_vpk, verify_download
from discovery_vpk_artwork import media_proposal
import discovery_enrichment_queue as queue
from discovery_requirements import extract_requirements, resolve_requirements, relevant_document

BASE = "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/"
MAX_FILE_BYTES = 262144


def safe_project(value, platform):
    parts = str(value or "").split("/")
    if platform not in {"github", "gitlab"} or len(parts) < 2 or len(parts) > 12:
        raise ValueError("Invalid project")
    if platform == "github" and len(parts) != 2:
        raise ValueError("Invalid GitHub project")
    if any(p in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", p) for p in parts):
        raise ValueError("Invalid project path")
    return "/".join(quote(p, safe="") for p in parts)


def provider_repo(entry):
    platform = entry["platform"]
    full = safe_project(entry["project"], platform)
    if platform == "github":
        endpoint = "https://api.github.com/repos/" + full
        repo = discovery.request(endpoint)
        if not isinstance(repo, dict) or str(repo.get("full_name", "")).casefold() != entry["project"].casefold():
            raise ValueError("Repository identity changed")
        branch = repo.get("default_branch")
        if not branch:
            raise ValueError("Missing branch")
        commit = discovery.request(endpoint + "/commits/" + quote(branch, safe=""))
        ref = commit.get("sha", "") if isinstance(commit, dict) else ""
    else:
        endpoint = "https://gitlab.com/api/v4/projects/" + quote(entry["project"], safe="")
        repo = discovery.request(endpoint, gitlab=True)
        if not isinstance(repo, dict) or str(repo.get("path_with_namespace", "")).casefold() != entry["project"].casefold():
            raise ValueError("Repository identity changed")
        branch = repo.get("default_branch")
        if not branch:
            raise ValueError("Missing branch")
        commit = discovery.request(endpoint + "/repository/commits/" + quote(branch, safe=""), gitlab=True)
        ref = commit.get("id", "") if isinstance(commit, dict) else ""
    expected = ("gh:" if platform == "github" else "gl:") + str(repo.get("id"))
    if entry.get("key") != expected or not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Repository ID or immutable ref could not be verified")
    return {**repo, "default_branch": ref}, endpoint, ref


def read_file(endpoint, platform, path, ref):
    """Use only official provider endpoints; reject large/binary data before parsing."""
    if platform == "github":
        project = endpoint.removeprefix("https://api.github.com/repos/")
        return discovery.raw_bytes(project, ref, path, MAX_FILE_BYTES)
    else:
        data = discovery.request(endpoint + "/repository/files/" + quote(path, safe=""), {"ref": ref}, gitlab=True)
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None
    size = data.get("size")
    encoded = data.get("content")
    if (not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES or
            not isinstance(encoded, str) or len(encoded) > MAX_FILE_BYTES * 2):
        return None
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except ValueError:
        return None
    return raw if len(raw) == size else None


def inspect(entry, taxonomy, official, retired, max_files):
    repo, endpoint, ref = provider_repo(entry)
    platform = entry["platform"]
    files = discovery.files_for(repo, platform)
    paths = prioritized_paths(files)
    evidence = []
    requirements = []
    app = entry.get("app_draft") or {}
    readmes = []
    skipped = []
    errors = []
    read_count = 0
    # Inspect the release metadata already exposed by the official APIs. A
    # different ID in an older release is conservatively treated as ambiguous.
    try:
        releases = discovery.request(endpoint + "/releases", {"per_page": 5}, gitlab=platform == "gitlab") or []
    except discovery.DiscoveryBudgetReached:
        raise
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        errors.append({"source": "releases", "error_type": type(exc).__name__})
        releases = []
    for release in releases[:5] if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name", ""))[:120]
        body = str(release.get("body") or release.get("description") or "")[:40000]
        evidence.extend(text_evidence(body, "release:" + tag, "release", tag))
        # Only use release-specific requirements for the draft's actual VPK.
        assets = release.get("assets") or []
        if isinstance(assets, dict):
            assets = assets.get("links") or []
        download_urls = {link.get("url") for link in app.get("links", [])
                         if isinstance(link, dict) and link.get("type") == "Download"}
        if any(isinstance(asset, dict) and any(asset.get(k) in download_urls
               for k in ("browser_download_url", "direct_asset_url", "url") if asset.get(k))
               for asset in assets if isinstance(assets, list)):
            requirements.extend(extract_requirements(body, "release:" + tag, tag))
    for path in paths[:max_files]:
        try:
            raw = read_file(endpoint, platform, path, ref)
        except discovery.DiscoveryBudgetReached:
            raise
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            errors.append({"source": path, "error_type": type(exc).__name__})
            skipped.append(path)
            continue
        if raw is None:
            skipped.append(path)
            continue
        read_count += 1
        if path.rsplit("/", 1)[-1].casefold() == "param.sfo":
            tid = sfo_title_id(raw)
            if tid:
                evidence.append({"title_id": tid, "source": path, "ref": ref,
                                 "kind": "repository_sfo", "confidence": "high"})
            continue
        if b"\0" in raw:
            skipped.append(path)
            continue
        try:
            body = raw.decode("utf-8-sig")
        except UnicodeError:
            skipped.append(path)
            continue
        name = path.rsplit("/", 1)[-1].casefold()
        doc = name.startswith(("readme", "changelog", "release", "install")) or name.endswith((".md", ".rst", ".txt"))
        build = name in {"cmakelists.txt", "makefile", "gnumakefile", "projectsettings.asset"} or name.endswith((".cmake", ".mk"))
        kind = "build" if build else "documentation" if doc else "code"
        if name.startswith("readme"):
            readmes.append((path, body[:12000]))
        evidence.extend(text_evidence(body, path, kind, ref))
        if relevant_document(path):
            requirements.extend(extract_requirements(body, path, ref))
    identity = resolve_title(evidence, official, retired)
    if platform == "github":
        owner = repo.get("owner") or {}
        owner_info = {"url": owner.get("html_url"), "id": owner.get("id"),
                      "fork": bool(repo.get("fork")), "group": owner.get("type") != "User"}
    else:
        owner = repo.get("owner") or {}
        namespace = repo.get("namespace") or {}
        owner_info = {"url": namespace.get("web_url"), "id": owner.get("id"),
                      "fork": bool(repo.get("forked_from_project")), "group": namespace.get("kind") != "user"}
    # Root README describes the project; nested READMEs often describe dependencies.
    purpose_readme = min(readmes, key=lambda pair: (pair[0].count("/"), pair[0].casefold()))[1] if readmes else ""
    category = classify_category(repo.get("name") or app.get("name", ""),
                                 repo.get("description") or app.get("description", ""),
                                 purpose_readme, entry.get("project_type"), taxonomy)
    return {"key": entry["key"], "project": entry["project"], "ref": ref,
            "title": identity, "category": category,
            "requirements": resolve_requirements(requirements),
            "owner_profile": owner_info, "vpk_asset": select_vpk(entry, releases),
            "coverage": {"listed_files": len(files), "eligible_files": len(paths),
                         "read_files": read_count, "skipped": skipped, "remaining": max(0, len(paths) - max_files),
                         "complete": False, "note": "Bounded pilot; tree/release coverage is not proof of exhaustive inspection"},
            "icon_present": bool(app.get("icon")), "screenshots_count": len(app.get("screenshots") or []),
            "publication_allowed": False, "errors": errors,
            "state": "partial" if errors else "inspected", "retry": bool(errors)}


def inspect_batch(rows, taxonomy, official, retired, max_files):
    """Isolate candidate failures, reserving global stop for exhausted budgets.

    Unexpected defects are reported separately so CI does not pass silently.
    """
    results = []
    stopped = False
    unexpected = 0
    for key, entry in rows:
        project = entry.get("project", "") if isinstance(entry, dict) else ""
        identity = {"key": key, "project": project}
        try:
            if not isinstance(entry, dict) or entry.get("key") != key:
                raise ValueError("Malformed candidate identity")
            results.append(inspect(entry, taxonomy, official, retired, max_files))
        except discovery.DiscoveryBudgetReached as exc:
            results.append({**identity, "state": "deferred", "error": str(exc),
                            "retry": True, "publication_allowed": False})
            stopped = True
            break
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as exc:
            results.append({**identity, "state": "candidate_error",
                            "error_type": type(exc).__name__, "retry": True,
                            "publication_allowed": False})
        except Exception as exc:
            # Do not swallow programming defects: retain diagnostics and fail
            # CI after processing the other independent candidates.
            unexpected += 1
            results.append({**identity, "state": "internal_error",
                            "error_type": type(exc).__name__,
                            "trace": [f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                                      for f in traceback.extract_tb(exc.__traceback__)],
                            "retry": True, "publication_allowed": False})
    pending = [key for key, _ in rows[len(results):]]
    return {"results": results, "budget_stopped": stopped,
            "unattempted_keys": pending, "internal_errors": unexpected}


def run(limit, offset, max_files, state_path=None, media_dir=None):
    state = None
    if state_path is not None:
        state = queue.load_state(state_path)
        if queue.paused(state):
            return {"mode": "queue_preview", "offset": offset, "requested": limit,
                    "max_files": max_files, "http_requests": 0,
                    "quota": {**discovery.quota_status(), "rate_limited": True,
                              "reset_at": state["resume_at"], "reason": "Persisted quota cooldown"},
                    "results": [], "internal_errors": 0, "budget_stopped": True,
                    "unattempted_keys": [], "queue": queue.summary(state, [])}
    taxonomy = to_v1(discovery.request(BASE + "categories.json"))
    catalog = to_v1(discovery.request(BASE + "catalog.json"))
    registry = discovery.request(BASE + "registry/retired_ids.json")
    authors = to_v1(discovery.request(BASE + "authors.json"))
    if not isinstance(authors, list) or not authors:
        raise ValueError("Official author profiles unavailable")
    if not isinstance(taxonomy, list) or not taxonomy or not isinstance(registry, dict) or not isinstance(registry.get("apps", {}).get("title_ids"), list):
        raise ValueError("Official taxonomy or retired registry unavailable")
    apps = discovery.official_apps(catalog)
    if not apps:
        raise ValueError("Empty official catalog")
    official = {str(a.get("title_id", "")).upper() for a in apps if a.get("title_id")}
    retired = registry["apps"]["title_ids"]
    candidates = json.loads(Path("data/discovery/candidates.json").read_text(encoding="utf-8"))
    if not isinstance(candidates, dict):
        raise ValueError("Invalid candidate inventory")
    if state is not None:
        state = queue.reconcile(state, candidates)
        queue.save_state(state_path, state)
        rows = queue.choose(state, candidates, limit, prioritize_publication=True)
    else:
        rows = sorted(candidates.items())[offset:offset+limit]
    batch = inspect_batch(rows, taxonomy, official, retired, max_files)
    binary_budget = 128 * 1024 * 1024
    deadline = min(time.monotonic() + 180, discovery.START_TIME + discovery.MAX_SECONDS)
    author_cache = {}
    for result in batch["results"]:
        if result.get("state") not in {"inspected", "partial"}:
            if state is not None:
                queue.checkpoint(state, result["key"], result, quota=discovery.quota_status())
                queue.save_state(state_path, state)
            continue
        entry = candidates[result["key"]]
        owner = result.get("owner_profile", {})
        cache_key = (entry.get("author_profile"), owner.get("id"), owner.get("fork"), owner.get("group"))
        try:
            if cache_key not in author_cache:
                author_cache[cache_key] = author_proposal(entry, owner, authors, registry,
                    queue.profile_request(state, owner, discovery.request) if state is not None else discovery.request)
            result["author"] = author_cache[cache_key]
        except discovery.DiscoveryBudgetReached:
            result["author"] = {"state": "deferred", "reason": "API budget exhausted"}
        except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError, AttributeError) as exc:
            result["author"] = {"state": "retry", "error_type": type(exc).__name__}
        title = result["title"]
        if title["state"] in {"collision", "retired", "ambiguous"} or result["category"]["state"] == "target_review":
            result["vpk"] = {"state": "review", "reason": "Resolve identity or platform conflict first"}
        else:
            try:
                cached = queue.cached_vpk(state["records"][result["key"]], result["vpk_asset"], title.get("suggestion", "")) if state is not None else None
                if cached is not None and ("display_name_field" not in cached or
                        (media_dir is not None and cached.get("artwork", {}).get("version") != 1)):
                    cached = None  # Revalidate old records that lack native title/artwork inspection.
                result["vpk"] = cached or verify_download(result["vpk_asset"], title.get("suggestion", ""), deadline, binary_budget, media_dir)
                if result["vpk"].get("artwork"):
                    artwork = result["vpk"]["artwork"]
                    result["media_proposal"] = media_proposal(entry.get("app_draft") or {}, artwork, discovery.media_image_fallback)
                    result["media_proposal"]["artifact_scope"] = "previous verification; re-extract before publication" if cached else "current run artifact"
            except Exception as exc:
                # Preserve the rest of the batch and mark CI failed for defects.
                batch["internal_errors"] += 1
                result["vpk"] = {"state": "internal_error", "error_type": type(exc).__name__}
                binary_budget = 0  # Unknown consumption: do not exceed the byte cap.
            if not result["vpk"].get("cache_hit"):
                binary_budget -= result["vpk"].get("bytes_read", 0)
            if result["vpk"]["state"] == "verified":
                native = result["vpk"].get("display_name")
                result["name_proposal"] = {"name": native or (entry.get("app_draft") or {}).get("name") or entry["project"].split("/")[-1],
                    "source": "sce_sys/param.sfo:" + result["vpk"]["display_name_field"] if native else "repository_fallback",
                    "vpk_sha256": result["vpk"]["sha256"]}
                tid = result["vpk"]["title_id"]
                evidence = title["evidence"] + [{"title_id": tid, "source": result["vpk"]["url"],
                    "ref": result["vpk"]["sha256"], "kind": "downloaded_vpk_sfo", "confidence": "high"}]
                result["title"] = resolve_title(evidence, official, retired)
        try:
            # Use existing, exact-account author matches; new drafts remain pending
            # until PSVitaAlive can validate both records atomically.
            draft = dict(entry.get("app_draft") or {})
            if result.get("name_proposal"):
                draft["name"] = result["name_proposal"]["name"]
            if result["author"]["state"] == "existing":
                draft["author_ids"] = result["author"]["author_ids"]
            result["readiness"] = readiness({**entry, "app_draft": draft}, result, taxonomy, authors, apps, registry)
            result["readiness"]["vpk_verified"] = result["vpk"]["state"] == "verified"
            result["readiness"]["icon_recoverable"] = bool(result.get("media_proposal", {}).get("icon"))
            result["readiness"]["fallback_images_available"] = len(result.get("media_proposal", {}).get("fallback_screenshots", []))
            result["readiness"]["publication_allowed"] = False
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            result["readiness"] = {"state": "review", "error_type": type(exc).__name__, "publication_allowed": False}
        if state is not None:
            queue.checkpoint(state, result["key"], result)
            queue.save_state(state_path, state)
    # Different accounts may normalize to the same slug. Detect collisions
    # within this sample; final canonical publication must recheck globally.
    proposed = {}
    for result in batch["results"]:
        author = result.get("author", {})
        if author.get("state") == "draft":
            proposed.setdefault(author["draft"]["id"], set()).add(author["profile"])
    for result in batch["results"]:
        author = result.get("author", {})
        if author.get("state") == "draft" and len(proposed[author["draft"]["id"]]) > 1:
            result["author"] = {**author, "state": "review", "reason": "Different accounts produce same author ID"}
    if state is not None:
        queue.apply_quota_pause(state, discovery.quota_status())
        queue.save_state(state_path, state)
        batch["queue"] = queue.summary(state, [key for key, _ in rows], batch["results"])
    return {"mode": "queue_preview" if state is not None else "read_only", "offset": offset,
            "requested": limit, "max_files": max_files,
            "http_requests": discovery.REQUEST_COUNT, "quota": discovery.quota_status(), **batch}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-files", type=int, default=18)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--queue-state", type=Path, help="Persistent pilot state; only enrichment-state.json in current directory")
    parser.add_argument("--media-dir", type=Path, help="Stage verified VPK images in enrichment-media only")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20 or args.offset < 0 or not 1 <= args.max_files <= 40:
        parser.error("limit 1–20, offset >=0, max-files 1–40")
    if args.queue_state is not None and (args.queue_state != Path("enrichment-state.json") or args.offset != 0):
        parser.error("queue-state must be enrichment-state.json and cannot be combined with offset")
    if args.media_dir is not None and args.media_dir != Path("enrichment-media"):
        parser.error("media-dir must be enrichment-media in the working directory")
    result = run(args.limit, args.offset, args.max_files, args.queue_state, args.media_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        # Reports only: don't permit overwriting canonical state or source files.
        if args.output.parent != Path(".") or not re.fullmatch(r"enrichment-report(?:-[A-Za-z0-9_-]+)?\.json", args.output.name):
            parser.error("output must be enrichment-report*.json in the working directory")
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if result["internal_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
