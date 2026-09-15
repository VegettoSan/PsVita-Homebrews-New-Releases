#!/usr/bin/env python3
"""Enriquece el historial y el dashboard con metadatos de PSVitaAlive.

Se ejecuta después de monitor.py para asociar cada repositorio con los registros
canónicos del catálogo (nombre, icono, categoría, descripción, etc.).
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

CATALOG_URL = os.getenv(
    "PSVITAALIVE_CATALOG_URL",
    "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json",
)
HISTORY_FILE = Path("data/releases.json")
SYNC_FILE = Path("data/sync.json")
HTML_FILE = Path("docs/index.html")
HTTP_TIMEOUT = 45
USER_AGENT = "PsVita-Homebrews-New-Releases/2.1"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_repo_url(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None

    host = parsed.netloc.lower().split(":", 1)[0]
    parts = [part for part in parsed.path.split("/") if part]

    if host in {"github.com", "www.github.com"} and len(parts) >= 2:
        return "github", f"{parts[0]}/{parts[1].removesuffix('.git')}"

    if host in {"gitlab.com", "www.gitlab.com"} and len(parts) >= 2:
        if "-" in parts:
            parts = parts[: parts.index("-")]
        if len(parts) < 2:
            return None
        parts[-1] = parts[-1].removesuffix(".git")
        return "gitlab", "/".join(parts)

    return None


def repo_key(platform: str, project: str) -> str:
    return f"{platform}:{project}".lower()


def app_urls(record: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in (
        "source_url",
        "release_page",
        "repository",
        "repository_url",
        "repo_url",
        "homepage",
        "website",
    ):
        value = record.get(key)
        if isinstance(value, str):
            urls.append(value)

    links = record.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and isinstance(link.get("url"), str):
                urls.append(link["url"])
    return urls


def looks_like_app(record: dict[str, Any]) -> bool:
    if not isinstance(record.get("name"), str) or not record.get("name", "").strip():
        return False
    markers = ("id", "title_id", "category_id", "icon", "description", "long_description", "version")
    return any(key in record for key in markers)


def compact_app(record: dict[str, Any]) -> dict[str, Any]:
    author_ids = record.get("author_ids") if isinstance(record.get("author_ids"), list) else []
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or "").strip(),
        "icon": str(record.get("icon") or "").strip(),
        "category": str(record.get("category_id") or "").strip(),
        "description": str(record.get("description") or "").strip(),
        "version": str(record.get("version") or "").strip(),
        "author_ids": [str(x) for x in author_ids if x],
    }


def catalog_repo_index(catalog: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}

    def add_record(record: dict[str, Any]) -> None:
        if not looks_like_app(record):
            return
        app = compact_app(record)
        if not app["name"]:
            return

        seen_keys: set[str] = set()
        for value in app_urls(record):
            normalized = normalize_repo_url(value)
            if not normalized:
                continue
            platform, project = normalized
            key = repo_key(platform, project)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            bucket = index.setdefault(key, [])
            if not any(existing.get("id") == app.get("id") and existing.get("name") == app.get("name") for existing in bucket):
                bucket.append(app)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            add_record(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(catalog)
    return index


def normalized_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def choose_primary_app(apps: list[dict[str, Any]], release: dict[str, Any]) -> dict[str, Any] | None:
    if not apps:
        return None
    if len(apps) == 1:
        return apps[0]

    haystack = normalized_text(
        " ".join(
            str(release.get(key) or "")
            for key in ("name", "tag", "body", "repo")
        )
    )

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for position, app in enumerate(apps):
        name = normalized_text(str(app.get("name") or ""))
        app_id = normalized_text(str(app.get("id") or ""))
        score = 0
        if name and name in haystack:
            score += 10
        if app_id and app_id in haystack:
            score += 6
        for token in name.split():
            if len(token) >= 4 and token in haystack:
                score += 1
        scored.append((score, -position, app))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def enrich_release(release: dict[str, Any], apps: list[dict[str, Any]]) -> None:
    if not apps:
        release.pop("catalog_apps", None)
        return

    primary = choose_primary_app(apps, release) or apps[0]
    ordered = [primary] + [app for app in apps if app is not primary]

    release["homebrew_name"] = primary.get("name") or release.get("name") or release.get("repo")
    release["homebrew_icon"] = primary.get("icon") or ""
    release["homebrew_category"] = primary.get("category") or ""
    release["homebrew_description"] = primary.get("description") or ""
    release["homebrew_id"] = primary.get("id") or ""
    release["homebrew_authors"] = primary.get("author_ids") or []
    release["catalog_apps"] = ordered[:12]


def format_size(size: int | float | None) -> str:
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return ""


def choose_primary_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None
    for extension in (".vpk", ".skprx", ".suprx", ".self", ".zip"):
        for asset in assets:
            if str(asset.get("name") or "").lower().endswith(extension):
                return asset
    return assets[0]


def esc(value: Any, *, quote: bool = False) -> str:
    return html.escape(str(value or ""), quote=quote)


def generate_dashboard(history: list[dict[str, Any]], sync_info: dict[str, Any]) -> None:
    cards: list[str] = []

    for item in history:
        platform = str(item.get("platform") or "github")
        repo = esc(item.get("repo"))
        release_name = esc(item.get("name") or item.get("tag") or "Release")
        homebrew_name = esc(item.get("homebrew_name") or item.get("name") or item.get("repo") or "Homebrew")
        icon = esc(item.get("homebrew_icon"), quote=True)
        category = esc(item.get("homebrew_category") or "homebrew")
        tag = esc(item.get("tag") or "sin tag")
        release_url = esc(item.get("url") or "#", quote=True)
        published = esc(str(item.get("published") or "")[:16].replace("T", " "))

        description_raw = str(item.get("body") or item.get("homebrew_description") or "Sin descripción").strip()
        description = esc(description_raw).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

        prerelease = '<span class="badge pre">PRE-RELEASE</span>' if item.get("prerelease") else ""

        apps = item.get("catalog_apps") if isinstance(item.get("catalog_apps"), list) else []
        extra_apps = [app for app in apps[1:] if isinstance(app, dict) and app.get("name")]
        related = ""
        if extra_apps:
            names = " · ".join(esc(app.get("name")) for app in extra_apps[:6])
            more = len(extra_apps) - 6
            suffix = f" · +{more} más" if more > 0 else ""
            related = f'<div class="related"><span>También asociado:</span> {names}{suffix}</div>'

        if icon:
            icon_html = (
                f'<img class="app-icon" src="{icon}" alt="Icono de {homebrew_name}" '
                'loading="lazy" referrerpolicy="no-referrer" onerror="this.parentElement.classList.add(\'icon-failed\');this.remove()">'
            )
        else:
            icon_html = ""

        assets = item.get("assets") if isinstance(item.get("assets"), list) else []
        primary_asset = choose_primary_asset(assets)
        buttons: list[str] = []

        if primary_asset:
            url = esc(primary_asset.get("download_url") or "#", quote=True)
            name = esc(primary_asset.get("name") or "Descargar")
            size = format_size(primary_asset.get("size"))
            size_text = f" · {esc(size)}" if size else ""
            buttons.append(f'<a class="btn primary" href="{url}" target="_blank" rel="noopener">⬇ {name}{size_text}</a>')

        for asset in assets:
            if asset is primary_asset:
                continue
            url = esc(asset.get("download_url") or "#", quote=True)
            name = esc(asset.get("name") or "archivo")
            size = format_size(asset.get("size"))
            size_text = f" · {esc(size)}" if size else ""
            buttons.append(f'<a class="btn" href="{url}" target="_blank" rel="noopener">{name}{size_text}</a>')

        if not buttons:
            buttons.append('<span class="no-assets">Sin assets publicados</span>')

        search_text = esc(
            " ".join(
                [
                    str(item.get("homebrew_name") or ""),
                    " ".join(str(app.get("name") or "") for app in apps if isinstance(app, dict)),
                    str(item.get("repo") or ""),
                    str(item.get("name") or ""),
                    str(item.get("tag") or ""),
                    str(item.get("homebrew_category") or ""),
                    description_raw,
                ]
            ).lower(),
            quote=True,
        )

        cards.append(
            f"""
            <article class="card {platform}" data-platform="{platform}" data-search="{search_text}">
              <div class="card-layout">
                <div class="icon-wrap">{icon_html}<span class="icon-fallback">PSV</span></div>
                <div class="card-content">
                  <div class="card-top">
                    <div>
                      <div class="eyebrow">{category.upper()} · {platform.upper()} · {repo}</div>
                      <h2><a href="{release_url}" target="_blank" rel="noopener">{homebrew_name}</a></h2>
                      <div class="release-name">{release_name}</div>
                    </div>
                    {prerelease}
                  </div>
                  <div class="meta">
                    <span class="tag">{tag}</span>
                    <span>{published or "fecha desconocida"} UTC</span>
                  </div>
                  {related}
                  <div class="body">{description}</div>
                  <div class="assets">{''.join(buttons)}</div>
                </div>
              </div>
            </article>
            """
        )

    cards_html = "\n".join(cards) if cards else '<div class="empty">Aún no hay releases registrados.</div>'
    repos = int(sync_info.get("repositories", 0))
    github_count = int(sync_info.get("github", 0))
    gitlab_count = int(sync_info.get("gitlab", 0))
    successful_checks = int(sync_info.get("successful_checks", 0))
    failed_checks = int(sync_info.get("failed_checks", 0))
    updated = esc(str(sync_info.get("synced_at") or "")[:16].replace("T", " "))

    document = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Últimos releases de homebrew y ports de PS Vita, con nombres e iconos de PSVitaAlive.">
  <title>PS Vita · New Releases</title>
  <style>
    :root {{ color-scheme: dark; --bg:#090d16; --panel:#111827; --panel2:#0f172a; --line:#253047; --text:#eef2ff; --muted:#94a3b8; --accent:#8b5cf6; --accent2:#22d3ee; --github:#60a5fa; --gitlab:#fb923c; --green:#34d399; --yellow:#fbbf24; }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }}
    body {{ margin:0; min-height:100vh; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:radial-gradient(circle at top left,rgba(139,92,246,.16),transparent 36rem),radial-gradient(circle at top right,rgba(34,211,238,.10),transparent 32rem),var(--bg); color:var(--text); }}
    a {{ color:inherit; }} .wrap {{ width:min(1180px,calc(100% - 32px)); margin:0 auto; }}
    header {{ padding:58px 0 28px; }} .hero {{ border:1px solid var(--line); background:linear-gradient(145deg,rgba(17,24,39,.94),rgba(15,23,42,.88)); border-radius:26px; padding:30px; box-shadow:0 24px 80px rgba(0,0,0,.28); }}
    .kicker {{ color:var(--accent2); font-weight:800; letter-spacing:.14em; font-size:.76rem; text-transform:uppercase; }}
    h1 {{ margin:8px 0; font-size:clamp(2rem,5vw,3.8rem); line-height:1; letter-spacing:-.045em; }} .lead {{ margin:0; color:var(--muted); max-width:800px; font-size:1.02rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-top:24px; }} .stat {{ border:1px solid var(--line); background:rgba(9,13,22,.48); border-radius:16px; padding:14px; }} .stat strong {{ display:block; font-size:1.3rem; }} .stat span {{ color:var(--muted); font-size:.78rem; }}
    .toolbar {{ position:sticky; top:0; z-index:10; display:flex; gap:10px; flex-wrap:wrap; padding:14px 0; background:rgba(9,13,22,.86); backdrop-filter:blur(14px); }}
    input,select {{ min-height:44px; border:1px solid var(--line); border-radius:12px; background:var(--panel2); color:var(--text); padding:0 13px; outline:none; }} input {{ flex:1 1 340px; }} input:focus,select:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(139,92,246,.14); }} #count {{ margin-left:auto; align-self:center; color:var(--muted); font-size:.86rem; }}
    main {{ padding:4px 0 64px; }} .card {{ border:1px solid var(--line); border-left:4px solid var(--github); background:rgba(17,24,39,.88); border-radius:18px; padding:20px; margin:14px 0; box-shadow:0 10px 35px rgba(0,0,0,.13); }} .card.gitlab {{ border-left-color:var(--gitlab); }}
    .card-layout {{ display:grid; grid-template-columns:92px minmax(0,1fr); gap:18px; }} .icon-wrap {{ width:92px; height:92px; border:1px solid var(--line); border-radius:20px; background:linear-gradient(145deg,#182033,#0d1320); overflow:hidden; display:grid; place-items:center; position:relative; }} .app-icon {{ width:100%; height:100%; object-fit:cover; position:relative; z-index:2; }} .icon-fallback {{ position:absolute; font-weight:900; letter-spacing:.08em; color:#64748b; }}
    .card-top {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }} .eyebrow {{ color:var(--muted); font-size:.72rem; font-weight:750; letter-spacing:.045em; }} h2 {{ margin:4px 0 0; font-size:1.45rem; }} h2 a {{ text-decoration:none; }} h2 a:hover {{ color:var(--accent2); }} .release-name {{ color:#c4b5fd; font-size:.88rem; margin-top:3px; }}
    .badge {{ white-space:nowrap; padding:5px 9px; border-radius:999px; font-size:.66rem; font-weight:900; letter-spacing:.05em; }} .badge.pre {{ color:#111827; background:var(--yellow); }}
    .meta {{ display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; color:var(--muted); font-size:.8rem; }} .tag {{ color:#ddd6fe; background:rgba(139,92,246,.13); border:1px solid rgba(139,92,246,.28); padding:2px 7px; border-radius:7px; }}
    .related {{ color:#94a3b8; font-size:.78rem; margin:4px 0 10px; }} .related span {{ color:#67e8f9; font-weight:700; }}
    .body {{ color:#cbd5e1; font-size:.91rem; line-height:1.55; max-height:190px; overflow:auto; padding-right:6px; overflow-wrap:anywhere; }}
    .assets {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }} .btn {{ text-decoration:none; border:1px solid var(--line); background:#172033; color:#dbeafe; padding:8px 11px; border-radius:10px; font-size:.78rem; transition:.16s ease; }} .btn:hover {{ transform:translateY(-1px); border-color:#475569; }} .btn.primary {{ border-color:rgba(52,211,153,.38); background:rgba(52,211,153,.12); color:#a7f3d0; font-weight:800; }}
    .no-assets,.empty {{ color:var(--muted); }} footer {{ color:var(--muted); text-align:center; padding:0 0 40px; font-size:.8rem; }}
    @media (max-width:820px) {{ .stats {{ grid-template-columns:repeat(2,1fr); }} #count {{ width:100%; margin-left:0; }} }}
    @media (max-width:600px) {{ .wrap {{ width:min(100% - 20px,1180px); }} header {{ padding-top:22px; }} .hero {{ padding:20px; border-radius:20px; }} .stats {{ grid-template-columns:1fr 1fr; }} .card {{ padding:15px; }} .card-layout {{ grid-template-columns:64px minmax(0,1fr); gap:12px; }} .icon-wrap {{ width:64px; height:64px; border-radius:15px; }} .card-top {{ flex-direction:column; }} h2 {{ font-size:1.18rem; }} }}
  </style>
</head>
<body>
  <header class="wrap"><section class="hero"><div class="kicker">PSVitaAlive · Release Tracker</div><h1>PS Vita New Releases</h1><p class="lead">Releases detectados automáticamente, ahora identificados con el nombre e icono oficiales almacenados en el catálogo de PSVitaAlive.</p><div class="stats"><div class="stat"><strong>{repos}</strong><span>repos monitorizados</span></div><div class="stat"><strong>{github_count}</strong><span>GitHub</span></div><div class="stat"><strong>{gitlab_count}</strong><span>GitLab</span></div><div class="stat"><strong>{successful_checks}</strong><span>consultas correctas</span></div><div class="stat"><strong>{failed_checks}</strong><span>errores temporales</span></div></div></section></header>
  <div class="wrap toolbar"><input id="search" type="search" placeholder="Buscar homebrew, repositorio, versión o categoría…" autocomplete="off"><select id="platform"><option value="all">Todas las plataformas</option><option value="github">GitHub</option><option value="gitlab">GitLab</option></select><span id="count"></span></div>
  <main class="wrap" id="releases">{cards_html}</main>
  <footer class="wrap">Última sincronización del catálogo: {updated or 'N/A'} UTC · Nombres e iconos: PSVitaAlive</footer>
  <script>
    const search=document.getElementById('search'); const platform=document.getElementById('platform'); const cards=[...document.querySelectorAll('.card')]; const count=document.getElementById('count');
    function filterCards(){{const q=search.value.trim().toLowerCase();const p=platform.value;let visible=0;for(const card of cards){{const show=(!q||card.dataset.search.includes(q))&&(p==='all'||card.dataset.platform===p);card.hidden=!show;if(show)visible++;}}count.textContent=`${{visible}} de ${{cards.length}} releases`;}}
    search.addEventListener('input',filterCards); platform.addEventListener('change',filterCards); filterCards();
  </script>
</body>
</html>
"""
    HTML_FILE.parent.mkdir(parents=True, exist_ok=True)
    HTML_FILE.write_text(document, encoding="utf-8")


def main() -> int:
    history = load_json(HISTORY_FILE, [])
    sync_info = load_json(SYNC_FILE, {})
    if not isinstance(history, list):
        history = []
    if not isinstance(sync_info, dict):
        sync_info = {}

    response = requests.get(CATALOG_URL, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    catalog = response.json()
    index = catalog_repo_index(catalog)

    matched = 0
    with_icon = 0
    for release in history:
        if not isinstance(release, dict):
            continue
        key = repo_key(str(release.get("platform") or "github"), str(release.get("repo") or ""))
        apps = index.get(key, [])
        if apps:
            matched += 1
            enrich_release(release, apps)
            if release.get("homebrew_icon"):
                with_icon += 1

    save_json(HISTORY_FILE, history)
    sync_info["catalog_metadata_matches"] = matched
    sync_info["catalog_icons"] = with_icon
    sync_info["metadata_enriched_at"] = datetime.now(timezone.utc).isoformat()
    save_json(SYNC_FILE, sync_info)
    generate_dashboard(history, sync_info)

    print(f"🎨 Metadatos PSVitaAlive: {matched}/{len(history)} releases asociados; {with_icon} con icono.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
