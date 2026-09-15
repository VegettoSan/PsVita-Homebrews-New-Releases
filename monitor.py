#!/usr/bin/env python3
"""
PS Vita Homebrew Release Tracker.

- Sincroniza automáticamente repositorios GitHub/GitLab desde el catálogo público
  de VegettoSan/PSVitaAlive.
- Consulta el release más reciente de cada repositorio.
- Notifica únicamente releases nuevos por Discord.
- Mantiene historial JSON y genera un dashboard estático para GitHub Pages.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

# ==================== CONFIG ====================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "").strip()

CATALOG_URL = os.getenv(
    "PSVITAALIVE_CATALOG_URL",
    "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json",
)

REPOS_FILE = Path("repos.txt")
STATE_FILE = Path("data/state.json")
HISTORY_FILE = Path("data/releases.json")
SYNC_INFO_FILE = Path("data/sync.json")
HTML_FILE = Path("docs/index.html")

MAX_HISTORY = 500
MAX_WORKERS = 8
HTTP_TIMEOUT = 30
USER_AGENT = "PsVita-Homebrews-New-Releases/2.0"

# =================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠️ No se pudo leer {path}: {exc}")
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    attempts: int = 3,
) -> Any:
    merged_headers = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)

    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                headers=merged_headers,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code in (403, 429):
                retry_after = response.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else min(5 * attempt, 20)
                if attempt < attempts:
                    print(f"⏳ Límite temporal en {url}; reintento en {wait}s")
                    time.sleep(wait)
                    continue

            if response.status_code == 404:
                return None

            if 500 <= response.status_code < 600 and attempt < attempts:
                time.sleep(2 * attempt)
                continue

            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(2 * attempt)

    raise RuntimeError(f"Error consultando {url}: {last_exc}")


def normalize_repo_url(value: str) -> tuple[str, str] | None:
    """Convierte URLs GitHub/GitLab a (plataforma, proyecto)."""
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value.startswith(("http://", "https://")):
        return None

    try:
        parsed = urlparse(value)
    except ValueError:
        return None

    host = parsed.netloc.lower().split(":", 1)[0]
    parts = [p for p in parsed.path.split("/") if p]

    if host in {"github.com", "www.github.com"}:
        if len(parts) < 2:
            return None
        owner = parts[0]
        repo = parts[1].removesuffix(".git")
        if not owner or not repo:
            return None
        return ("github", f"{owner}/{repo}")

    if host in {"gitlab.com", "www.gitlab.com"}:
        if len(parts) < 2:
            return None

        if "-" in parts:
            parts = parts[: parts.index("-")]

        if len(parts) < 2:
            return None

        parts[-1] = parts[-1].removesuffix(".git")
        project = "/".join(parts)
        return ("gitlab", project)

    return None


def discover_repositories(catalog: Any) -> list[dict[str, str]]:
    """
    Recorre el catálogo sin depender de una estructura raíz concreta.
    Se inspeccionan URLs almacenadas en campos habituales y en objetos de links.
    """
    discovered: dict[str, dict[str, str]] = {}

    interesting_keys = {
        "url",
        "source_url",
        "release_page",
        "repository",
        "repository_url",
        "repo_url",
        "source",
        "homepage",
        "website",
    }

    def add_url(value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = normalize_repo_url(value)
        if not normalized:
            return
        platform, project = normalized
        key = f"{platform}:{project.lower()}"
        discovered[key] = {
            "platform": platform,
            "project": project,
            "key": f"{platform}:{project}",
            "url": (
                f"https://github.com/{project}"
                if platform == "github"
                else f"https://gitlab.com/{project}"
            ),
        }

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = str(key).lower()
                if key_lower in interesting_keys:
                    add_url(value)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(catalog)

    repos = list(discovered.values())
    repos.sort(key=lambda r: (r["platform"], r["project"].lower()))
    return repos


def sync_catalog_repositories() -> tuple[list[dict[str, str]], dict[str, Any]]:
    print(f"📚 Descargando catálogo PSVitaAlive: {CATALOG_URL}")
    catalog = request_json(CATALOG_URL)
    if catalog is None:
        raise RuntimeError("No se pudo descargar catalog.json de PSVitaAlive")

    repos = discover_repositories(catalog)
    github_count = sum(1 for r in repos if r["platform"] == "github")
    gitlab_count = sum(1 for r in repos if r["platform"] == "gitlab")

    lines = [
        "# AUTO-GENERATED — no editar manualmente.",
        "# Fuente: https://github.com/VegettoSan/PSVitaAlive/blob/main/catalog.json",
        "# Se regenera cada ejecución del monitor.",
        "",
    ]
    lines.extend(r["url"] for r in repos)
    REPOS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    info = {
        "catalog_url": CATALOG_URL,
        "synced_at": iso_now(),
        "repositories": len(repos),
        "github": github_count,
        "gitlab": gitlab_count,
    }
    print(
        f"✅ Catálogo sincronizado: {len(repos)} repos "
        f"({github_count} GitHub / {gitlab_count} GitLab)"
    )
    return repos, info


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_github_release(project: str) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{quote(project, safe='/')}/releases"
    data = request_json(
        url,
        headers=github_headers(),
        params={"per_page": 5},
    )
    if not data or not isinstance(data, list):
        return None

    latest = next((item for item in data if not item.get("draft")), None)
    if not latest:
        return None

    assets = [
        {
            "name": asset.get("name") or "archivo",
            "size": asset.get("size", 0) or 0,
            "download_url": asset.get("browser_download_url") or "",
            "content_type": asset.get("content_type") or "",
        }
        for asset in latest.get("assets", [])
        if asset.get("browser_download_url")
    ]

    return {
        "platform": "github",
        "repo": project,
        "release_id": str(latest.get("id") or latest.get("tag_name") or ""),
        "tag": latest.get("tag_name") or "",
        "name": latest.get("name") or latest.get("tag_name") or "Release",
        "url": latest.get("html_url") or f"https://github.com/{project}/releases",
        "published": latest.get("published_at") or latest.get("created_at") or "",
        "prerelease": bool(latest.get("prerelease")),
        "draft": False,
        "body": latest.get("body") or "",
        "assets": assets,
        "author": (latest.get("author") or {}).get("login") or "",
    }


def get_gitlab_release(project: str) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    if GITLAB_TOKEN:
        headers["PRIVATE-TOKEN"] = GITLAB_TOKEN

    encoded_project = quote(project, safe="")
    url = f"https://gitlab.com/api/v4/projects/{encoded_project}/releases"
    data = request_json(url, headers=headers, params={"per_page": 5})
    if not data or not isinstance(data, list):
        return None

    latest = data[0]
    assets: list[dict[str, Any]] = []

    for link in (latest.get("assets") or {}).get("links", []):
        if not link.get("url"):
            continue
        assets.append(
            {
                "name": link.get("name") or link["url"].split("/")[-1] or "archivo",
                "size": 0,
                "download_url": link["url"],
                "content_type": "",
            }
        )

    for source in (latest.get("assets") or {}).get("sources", []):
        if not source.get("url"):
            continue
        fmt = source.get("format") or "zip"
        assets.append(
            {
                "name": f"source.{fmt}",
                "size": 0,
                "download_url": source["url"],
                "content_type": "",
            }
        )

    tag = latest.get("tag_name") or ""
    links = latest.get("_links") or {}

    return {
        "platform": "gitlab",
        "repo": project,
        "release_id": tag,
        "tag": tag,
        "name": latest.get("name") or tag or "Release",
        "url": links.get("self") or f"https://gitlab.com/{project}/-/releases/{quote(tag)}",
        "published": latest.get("released_at") or latest.get("created_at") or "",
        "prerelease": False,
        "draft": False,
        "body": latest.get("description") or "",
        "assets": assets,
        "author": ((latest.get("author") or {}).get("name") or ""),
    }


def fetch_latest_release(repo: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, str | None]:
    try:
        if repo["platform"] == "github":
            release = get_github_release(repo["project"])
        else:
            release = get_gitlab_release(repo["project"])
        return repo, release, None
    except Exception as exc:
        return repo, None, str(exc)


def format_size(size: int | float | None) -> str:
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return ""


def state_tag(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("tag") or value.get("release_id") or "")
    return ""


def release_identity(release: dict[str, Any]) -> str:
    return str(release.get("release_id") or release.get("tag") or release.get("published") or "")


def release_history_key(release: dict[str, Any]) -> str:
    return f"{release.get('platform')}:{release.get('repo')}:{release_identity(release)}"


def send_discord(release: dict[str, Any]) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False

    platform = release.get("platform", "github")
    color = 0x2F81F7 if platform == "github" else 0xFC6D26
    prerelease = " · 🟡 Pre-release" if release.get("prerelease") else ""

    body = re.sub(r"\s+", " ", str(release.get("body") or "")).strip()
    if len(body) > 900:
        body = body[:897] + "..."
    if not body:
        body = "Sin descripción."

    fields = [
        {"name": "Repositorio", "value": f"`{release.get('repo', '')}`", "inline": True},
        {"name": "Versión", "value": f"`{release.get('tag') or 'N/A'}`", "inline": True},
        {
            "name": "Publicado",
            "value": (release.get("published") or "N/A")[:10],
            "inline": True,
        },
    ]

    for asset in (release.get("assets") or [])[:8]:
        name = str(asset.get("name") or "archivo")[:240]
        size = format_size(asset.get("size"))
        suffix = f" · {size}" if size else ""
        fields.append(
            {
                "name": f"⬇ {name}",
                "value": f"[Descargar]({asset.get('download_url')}){suffix}",
                "inline": True,
            }
        )

    payload = {
        "username": "PS Vita Release Tracker",
        "content": f"🆕 **Nuevo release detectado**{prerelease}",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": str(release.get("name") or release.get("tag") or "Nuevo release")[:256],
                "url": release.get("url") or "",
                "description": body,
                "color": color,
                "fields": fields[:25],
                "footer": {"text": "Catálogo sincronizado con PSVitaAlive"},
                "timestamp": iso_now(),
            }
        ],
    }

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"❌ Discord: {exc}")
        return False


def parse_published(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def sort_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history.sort(key=lambda item: parse_published(item.get("published")), reverse=True)
    return history[:MAX_HISTORY]


def choose_primary_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None

    priority = (".vpk", ".skprx", ".suprx", ".self", ".zip")
    for extension in priority:
        for asset in assets:
            if str(asset.get("name") or "").lower().endswith(extension):
                return asset
    return assets[0]


def generate_html(
    history: list[dict[str, Any]],
    sync_info: dict[str, Any],
    *,
    successful_checks: int,
    failed_checks: int,
) -> None:
    cards: list[str] = []

    for item in history:
        platform = item.get("platform", "github")
        repo = html.escape(str(item.get("repo") or ""))
        tag = html.escape(str(item.get("tag") or ""))
        name = html.escape(str(item.get("name") or item.get("tag") or "Release"))
        release_url = html.escape(str(item.get("url") or "#"), quote=True)
        published = html.escape(str(item.get("published") or "")[:16].replace("T", " "))
        prerelease = '<span class="badge pre">PRE-RELEASE</span>' if item.get("prerelease") else ""

        body = html.escape(str(item.get("body") or "Sin descripción"))
        body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

        assets = item.get("assets") or []
        primary = choose_primary_asset(assets)
        buttons: list[str] = []

        if primary:
            p_url = html.escape(str(primary.get("download_url") or "#"), quote=True)
            p_name = html.escape(str(primary.get("name") or "Descargar"))
            p_size = format_size(primary.get("size"))
            size_text = f" · {html.escape(p_size)}" if p_size else ""
            buttons.append(
                f'<a class="btn primary" href="{p_url}" target="_blank" rel="noopener">'
                f'⬇ {p_name}{size_text}</a>'
            )

        for asset in assets:
            if primary is asset:
                continue
            a_url = html.escape(str(asset.get("download_url") or "#"), quote=True)
            a_name = html.escape(str(asset.get("name") or "archivo"))
            a_size = format_size(asset.get("size"))
            size_text = f" · {html.escape(a_size)}" if a_size else ""
            buttons.append(
                f'<a class="btn" href="{a_url}" target="_blank" rel="noopener">'
                f'{a_name}{size_text}</a>'
            )

        if not buttons:
            buttons.append('<span class="no-assets">Sin assets publicados</span>')

        search_text = html.escape(
            f"{item.get('repo', '')} {item.get('name', '')} {item.get('tag', '')} {item.get('body', '')}".lower(),
            quote=True,
        )

        cards.append(
            f"""
            <article class="card {platform}" data-platform="{platform}" data-search="{search_text}">
              <div class="card-top">
                <div>
                  <div class="eyebrow">{platform.upper()} · {repo}</div>
                  <h2><a href="{release_url}" target="_blank" rel="noopener">{name}</a></h2>
                </div>
                {prerelease}
              </div>
              <div class="meta">
                <span class="tag">{tag or "sin tag"}</span>
                <span>{published or "fecha desconocida"} UTC</span>
              </div>
              <div class="body">{body}</div>
              <div class="assets">{''.join(buttons)}</div>
            </article>
            """
        )

    cards_html = "\n".join(cards) if cards else '<div class="empty">Aún no hay releases registrados.</div>'

    repos = int(sync_info.get("repositories", 0))
    github_count = int(sync_info.get("github", 0))
    gitlab_count = int(sync_info.get("gitlab", 0))
    updated = html.escape(str(sync_info.get("synced_at", ""))[:16].replace("T", " "))

    document = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Últimos releases de homebrew y ports de PS Vita, sincronizados con PSVitaAlive.">
  <title>PS Vita · New Releases</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #090d16;
      --panel: #111827;
      --panel2: #0f172a;
      --line: #253047;
      --text: #eef2ff;
      --muted: #94a3b8;
      --accent: #8b5cf6;
      --accent2: #22d3ee;
      --github: #60a5fa;
      --gitlab: #fb923c;
      --green: #34d399;
      --yellow: #fbbf24;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(139,92,246,.16), transparent 36rem),
        radial-gradient(circle at top right, rgba(34,211,238,.10), transparent 32rem),
        var(--bg);
      color: var(--text);
    }}
    a {{ color: inherit; }}
    .wrap {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
    header {{ padding: 58px 0 28px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(145deg, rgba(17,24,39,.94), rgba(15,23,42,.88));
      border-radius: 26px;
      padding: 30px;
      box-shadow: 0 24px 80px rgba(0,0,0,.28);
    }}
    .kicker {{
      color: var(--accent2);
      font-weight: 800;
      letter-spacing: .14em;
      font-size: .76rem;
      text-transform: uppercase;
    }}
    h1 {{ margin: 8px 0 8px; font-size: clamp(2rem, 5vw, 3.8rem); line-height: 1; letter-spacing: -.045em; }}
    .lead {{ margin: 0; color: var(--muted); max-width: 760px; font-size: 1.02rem; }}
    .stats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-top: 24px; }}
    .stat {{
      border: 1px solid var(--line);
      background: rgba(9,13,22,.48);
      border-radius: 16px;
      padding: 14px;
    }}
    .stat strong {{ display: block; font-size: 1.3rem; }}
    .stat span {{ color: var(--muted); font-size: .78rem; }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding: 14px 0;
      background: rgba(9,13,22,.86);
      backdrop-filter: blur(14px);
    }}
    input, select {{
      min-height: 44px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel2);
      color: var(--text);
      padding: 0 13px;
      outline: none;
    }}
    input {{ flex: 1 1 340px; }}
    input:focus, select:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(139,92,246,.14); }}
    #count {{ margin-left: auto; align-self: center; color: var(--muted); font-size: .86rem; }}
    main {{ padding: 4px 0 64px; }}
    .card {{
      border: 1px solid var(--line);
      border-left: 4px solid var(--github);
      background: rgba(17,24,39,.88);
      border-radius: 18px;
      padding: 20px;
      margin: 14px 0;
      box-shadow: 0 10px 35px rgba(0,0,0,.13);
    }}
    .card.gitlab {{ border-left-color: var(--gitlab); }}
    .card-top {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    .eyebrow {{ color: var(--muted); font-size: .76rem; font-weight: 750; letter-spacing: .04em; }}
    h2 {{ margin: 5px 0 0; font-size: 1.28rem; }}
    h2 a {{ text-decoration: none; }}
    h2 a:hover {{ color: var(--accent2); }}
    .badge {{
      white-space: nowrap;
      padding: 5px 9px;
      border-radius: 999px;
      font-size: .66rem;
      font-weight: 900;
      letter-spacing: .05em;
    }}
    .badge.pre {{ color: #111827; background: var(--yellow); }}
    .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0; color: var(--muted); font-size: .8rem; }}
    .tag {{ color: #ddd6fe; background: rgba(139,92,246,.13); border: 1px solid rgba(139,92,246,.28); padding: 2px 7px; border-radius: 7px; }}
    .body {{
      color: #cbd5e1;
      font-size: .91rem;
      line-height: 1.55;
      max-height: 190px;
      overflow: auto;
      padding-right: 6px;
      overflow-wrap: anywhere;
    }}
    .assets {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }}
    .btn {{
      text-decoration: none;
      border: 1px solid var(--line);
      background: #172033;
      color: #dbeafe;
      padding: 8px 11px;
      border-radius: 10px;
      font-size: .78rem;
      transition: .16s ease;
    }}
    .btn:hover {{ transform: translateY(-1px); border-color: #475569; }}
    .btn.primary {{
      border-color: rgba(52,211,153,.38);
      background: rgba(52,211,153,.12);
      color: #a7f3d0;
      font-weight: 800;
    }}
    .no-assets, .empty {{ color: var(--muted); }}
    footer {{ color: var(--muted); text-align: center; padding: 0 0 40px; font-size: .8rem; }}
    @media (max-width: 820px) {{
      .stats {{ grid-template-columns: repeat(2, 1fr); }}
      #count {{ width: 100%; margin-left: 0; }}
    }}
    @media (max-width: 520px) {{
      .wrap {{ width: min(100% - 20px, 1180px); }}
      header {{ padding-top: 22px; }}
      .hero {{ padding: 20px; border-radius: 20px; }}
      .stats {{ grid-template-columns: 1fr 1fr; }}
      .card {{ padding: 16px; }}
      .card-top {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header class="wrap">
    <section class="hero">
      <div class="kicker">PSVitaAlive · Release Tracker</div>
      <h1>PS Vita New Releases</h1>
      <p class="lead">Releases de homebrew, ports, plugins y utilidades detectados automáticamente desde los repositorios enlazados por el catálogo de PSVitaAlive.</p>
      <div class="stats">
        <div class="stat"><strong>{repos}</strong><span>repos monitorizados</span></div>
        <div class="stat"><strong>{github_count}</strong><span>GitHub</span></div>
        <div class="stat"><strong>{gitlab_count}</strong><span>GitLab</span></div>
        <div class="stat"><strong>{successful_checks}</strong><span>consultas correctas</span></div>
        <div class="stat"><strong>{failed_checks}</strong><span>errores temporales</span></div>
      </div>
    </section>
  </header>

  <div class="wrap toolbar">
    <input id="search" type="search" placeholder="Buscar proyecto, release, versión o texto…" autocomplete="off">
    <select id="platform">
      <option value="all">Todas las plataformas</option>
      <option value="github">GitHub</option>
      <option value="gitlab">GitLab</option>
    </select>
    <span id="count"></span>
  </div>

  <main class="wrap" id="releases">
    {cards_html}
  </main>

  <footer class="wrap">
    Última sincronización del catálogo: {updated or "N/A"} UTC · Fuente: PSVitaAlive
  </footer>

  <script>
    const search = document.getElementById('search');
    const platform = document.getElementById('platform');
    const cards = [...document.querySelectorAll('.card')];
    const count = document.getElementById('count');

    function filterCards() {{
      const q = search.value.trim().toLowerCase();
      const p = platform.value;
      let visible = 0;

      for (const card of cards) {{
        const matchesText = !q || card.dataset.search.includes(q);
        const matchesPlatform = p === 'all' || card.dataset.platform === p;
        const show = matchesText && matchesPlatform;
        card.hidden = !show;
        if (show) visible++;
      }}

      count.textContent = `${{visible}} de ${{cards.length}} releases`;
    }}

    search.addEventListener('input', filterCards);
    platform.addEventListener('change', filterCards);
    filterCards();
  </script>
</body>
</html>
"""

    HTML_FILE.parent.mkdir(parents=True, exist_ok=True)
    HTML_FILE.write_text(document, encoding="utf-8")


def main() -> int:
    repos, sync_info = sync_catalog_repositories()

    old_state = load_json(STATE_FILE, {})
    if not isinstance(old_state, dict):
        old_state = {}

    history = load_json(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []

    # Baseline silencioso para una instalación nueva o para la primera migración al
    # catálogo completo de PSVitaAlive. Así evitamos cientos de avisos antiguos.
    initial_baseline = not bool(old_state)
    first_catalog_sync = not SYNC_INFO_FILE.exists()

    existing_history_keys = {
        release_history_key(item)
        for item in history
        if isinstance(item, dict)
    }

    new_state: dict[str, Any] = {}
    changed_releases: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    successful_checks = 0

    print(f"🔎 Consultando {len(repos)} repositorios con {MAX_WORKERS} workers…")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_latest_release, repo) for repo in repos]

        for future in concurrent.futures.as_completed(futures):
            repo, release, error = future.result()
            key = repo["key"]

            if error:
                errors.append({"repo": key, "error": error})
                if key in old_state:
                    new_state[key] = old_state[key]
                continue

            successful_checks += 1

            if not release:
                if key in old_state:
                    new_state[key] = old_state[key]
                continue

            identity = release_identity(release)
            previous = state_tag(old_state.get(key))

            new_state[key] = {
                "tag": release.get("tag") or identity,
                "release_id": identity,
                "published": release.get("published") or "",
                "checked_at": iso_now(),
            }

            history_key = release_history_key(release)
            if history_key not in existing_history_keys:
                history.append(release)
                existing_history_keys.add(history_key)

            if initial_baseline:
                continue

            if first_catalog_sync and key not in old_state:
                continue

            if previous != (release.get("tag") or identity):
                changed_releases.append(release)

    history = sort_history(history)

    notifications_sent = 0
    for release in sorted(
        changed_releases,
        key=lambda item: parse_published(item.get("published")),
    ):
        if send_discord(release):
            notifications_sent += 1

    sync_info.update(
        {
            "checked_at": iso_now(),
            "successful_checks": successful_checks,
            "failed_checks": len(errors),
            "new_releases": len(changed_releases),
            "discord_notifications": notifications_sent,
            "initial_baseline": initial_baseline,
            "first_catalog_sync": first_catalog_sync,
            "errors": errors[:100],
        }
    )

    save_json(STATE_FILE, new_state)
    save_json(HISTORY_FILE, history)
    save_json(SYNC_INFO_FILE, sync_info)
    generate_html(
        history,
        sync_info,
        successful_checks=successful_checks,
        failed_checks=len(errors),
    )

    print(
        f"🏁 Listo: {successful_checks} repos consultados, "
        f"{len(errors)} errores, {len(changed_releases)} releases nuevos, "
        f"{notifications_sent} notificaciones."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
