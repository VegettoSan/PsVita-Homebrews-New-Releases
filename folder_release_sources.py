#!/usr/bin/env python3
"""Discover independent homebrew VPKs in reviewed GitHub folder repositories."""
from __future__ import annotations
import base64
import re
from pathlib import PurePosixPath
from urllib.parse import quote

SUPPORTED_FOLDER_REPOS = {"papilenote/vita-ports": "main"}
SHA40 = re.compile(r"^[0-9a-f]{40}$", re.I)
VERSION = re.compile(
    r"^v?\d+(?:\.\d+){0,3}"
    r"(?:[-._]?(?:dev|test|alpha|a|beta|b|pre|preview|rc)(?:[-._]?\d+)?)?$", re.I,
)
TITLE_ROW = re.compile(r"^\s*\|\s*Title\s*ID\s*\|\s*([^|\n]+)\|", re.I | re.M)
VERSION_ROW = re.compile(r"^\s*\|\s*Version\s*\|\s*([^|\n]+)\|", re.I | re.M)


def read_port_identity(readme: str) -> tuple[str, str] | None:
    title, version = TITLE_ROW.search(readme), VERSION_ROW.search(readme)
    if not title or not version:
        return None
    tid = title.group(1).strip().strip(chr(96)).upper()
    tag = version.group(1).strip().strip(chr(96))
    if not re.fullmatch(r"[A-Z0-9]{9}", tid) or not VERSION.fullmatch(tag):
        return None
    return tid, tag


def pick_folder_vpk(entries: list[dict], folder: str, version: str) -> dict | None:
    vpks = [
        item for item in entries
        if item.get("type") == "blob"
        and isinstance(item.get("path"), str)
        and PurePosixPath(item["path"]).parent.as_posix() == folder
        and item["path"].lower().endswith(".vpk")
        and isinstance(item.get("size"), int) and not isinstance(item.get("size"), bool)
        and 1024 <= item["size"] <= 100_000_000
        and SHA40.fullmatch(str(item.get("sha") or ""))
    ]
    if len(vpks) == 1:
        return vpks[0]
    if not vpks:
        return None
    token = re.compile(r"(?<![a-z0-9])v?" + re.escape(version.lstrip("vV")) +
                       r"(?=\.vpk$)", re.I)
    matched = [item for item in vpks if token.search(PurePosixPath(item["path"]).name)]
    return matched[0] if len(matched) == 1 else None


def scan_supported_folders(repositories: list[dict], request_json, github_headers):
    """Return (valid per-folder records, review messages); no binary downloads."""
    releases, problems = [], []
    visible = {str(r.get("project") or "").lower() for r in repositories
               if r.get("platform") == "github"}
    for repo, branch in SUPPORTED_FOLDER_REPOS.items():
        if repo not in visible:
            continue
        base = f"https://api.github.com/repos/{repo}"
        headers = github_headers()
        try:
            head = request_json(f"{base}/commits/{branch}", headers=headers)
            sha = str(head.get("sha") or "")
            if not SHA40.fullmatch(sha):
                raise ValueError("invalid head commit SHA")
            tree = request_json(f"{base}/git/trees/{sha}?recursive=1", headers=headers)
            if not isinstance(tree, dict) or tree.get("truncated") or not isinstance(tree.get("tree"), list):
                raise ValueError("missing/truncated Git tree")
        except Exception as exc:
            problems.append(f"{repo}: tree unavailable: {exc}")
            continue
        entries = tree["tree"]
        folders = sorted(x["path"] for x in entries if isinstance(x, dict)
                         and x.get("type") == "tree" and isinstance(x.get("path"), str)
                         and "/" not in x["path"])
        for folder in folders:
            files = [x for x in entries if isinstance(x, dict)
                     and isinstance(x.get("path"), str)
                     and x["path"].startswith(folder + "/")]
            readmes = [x for x in files if x.get("type") == "blob"
                       and x["path"].casefold() == (folder + "/README.md").casefold()
                       and isinstance(x.get("size"), int) and 0 < x["size"] < 200_000]
            if len(readmes) != 1:
                problems.append(f"{repo}/{folder}: missing unique README")
                continue
            try:
                url = f"{base}/contents/{quote(readmes[0]['path'], safe='/')}"
                blob = request_json(url, headers=headers, params={"ref": sha})
                if not isinstance(blob, dict) or blob.get("encoding") != "base64":
                    raise ValueError("README not base64")
                content = base64.b64decode("".join(str(blob.get("content") or "").split()),
                                           validate=True)
                if not content or len(content) >= 200_000:
                    raise ValueError("README size invalid")
                identity = read_port_identity(content.decode("utf-8"))
                if identity is None:
                    raise ValueError("Title ID or version ambiguous")
                title_id, version = identity
                vpk = pick_folder_vpk(files, folder, version)
                if vpk is None:
                    raise ValueError("VPK not unique for README version")
                commits = request_json(f"{base}/commits", headers=headers, params={
                    "path": vpk["path"], "sha": sha, "per_page": 1})
                if not isinstance(commits, list) or len(commits) != 1:
                    raise ValueError("VPK commit unavailable")
                file_sha = str(commits[0].get("sha") or "")
                if not SHA40.fullmatch(file_sha):
                    raise ValueError("VPK commit invalid")
                when = str(((commits[0].get("commit") or {}).get("committer") or {}).get("date") or "")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", when):
                    raise ValueError("VPK commit date invalid")
                direct = (f"https://raw.githubusercontent.com/{repo}/{file_sha}/"
                          f"{quote(vpk['path'], safe='/')}")
                releases.append({
                    "platform": "github_folder", "repo": repo, "branch": branch,
                    "folder": folder, "title_id": title_id, "tag": version,
                    "published": when, "commit_sha": file_sha,
                    "blob_sha": vpk["sha"],
                    "assets": [{"name": PurePosixPath(vpk["path"]).name,
                                "download_url": direct, "size": vpk["size"]}],
                })
            except Exception as exc:
                problems.append(f"{repo}/{folder}: {exc}")
    return releases, problems
