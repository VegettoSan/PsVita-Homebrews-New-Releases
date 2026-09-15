#!/usr/bin/env python3
"""Enriquece el historial y genera el dashboard con metadatos de PSVitaAlive.

Funciones principales:
- Asocia cada release con su entrada de PSVitaAlive (nombre, icono, categoría, versión).
- Determina si PSVitaAlive está al día o pendiente frente al release más reciente.
- Elimina duplicados visuales y exactos del historial.
- Renderiza correctamente Markdown/HTML habitual de GitHub de forma segura.
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

import bleach
import mistune
import requests

CATALOG_URL = os.getenv(
    "PSVITAALIVE_CATALOG_URL",
    "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/catalog.json",
)
HISTORY_FILE = Path("data/releases.json")
SYNC_FILE = Path("data/sync.json")
HTML_FILE = Path("docs/index.html")
HTTP_TIMEOUT = 45
USER_AGENT = "PsVita-Homebrews-New-Releases/2.2"

MARKDOWN = mistune.create_markdown(
    escape=False,
    plugins=["strikethrough", "table", "task_lists", "url"],
)

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "del", "s",
    "code", "pre", "blockquote", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "img", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "details", "summary", "div", "span", "kbd", "sub", "sup",
    "input",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "div": ["align"],
    "span": ["class"],
    "code": ["class"],
    "th": ["align"],
    "td": ["align"],
    "input": ["type", "checked", "disabled"],
}
ALLOWED_PROTOCOLS = {"http", "https", "mailto"}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_published(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


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

    markers = (
        "id",
        "title_id",
        "category_id",
        "icon",
        "description",
        "long_description",
        "version",
    )
    return any(key in record for key in markers)


def compact_app(record: dict[str, Any]) -> dict[str, Any]:
    author_ids = record.get("author_ids") if isinstance(record.get("author_ids"), list) else []
    screenshots = record.get("screenshots") if isinstance(record.get("screenshots"), list) else []

    return {
        "id": str(record.get("id") or "").strip(),
        "name": str(record.get("name") or "").strip(),
        "icon": str(record.get("icon") or "").strip(),
        "category": str(record.get("category_id") or "").strip(),
        "description": str(record.get("description") or "").strip(),
        "long_description": str(record.get("long_description") or "").strip(),
        "version": str(record.get("version") or "").strip(),
        "version_date": str(record.get("version_date") or "").strip(),
        "author_ids": [str(x) for x in author_ids if x],
        "screenshots": [str(x) for x in screenshots if isinstance(x, str) and x],
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

            if not any(
                existing.get("id") == app.get("id")
                and existing.get("name") == app.get("name")
                for existing in bucket
            ):
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


def choose_primary_app(
    apps: list[dict[str, Any]],
    release: dict[str, Any],
) -> dict[str, Any] | None:
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


def version_signature(value: Any) -> tuple[tuple[int, ...] | None, str]:
    """Normaliza formatos como v.8.3.0, v8.3.0, Version 8.3.0, etc."""
    text = html.unescape(str(value or "")).strip().lower()

    if not text:
        return None, ""

    text = re.sub(r"^\s*(?:version|release)\s*", "", text)
    text = re.sub(r"^\s*v[\s._-]*", "", text)

    numbers = [int(piece) for piece in re.findall(r"\d+", text)]
    if numbers:
        while len(numbers) > 1 and numbers[-1] == 0:
            numbers.pop()

        suffix = re.sub(r"\d+", "", text)
        suffix = re.sub(r"[^a-z]+", "", suffix)
        return tuple(numbers), suffix

    fallback = re.sub(r"[^a-z0-9]+", "", text)
    return None, fallback


def compare_versions(catalog_version: Any, release_version: Any) -> int | None:
    """-1 catálogo más viejo, 0 equivalente, 1 catálogo más nuevo, None no comparable."""
    c_numbers, c_suffix = version_signature(catalog_version)
    r_numbers, r_suffix = version_signature(release_version)

    if c_numbers is not None and r_numbers is not None:
        max_len = max(len(c_numbers), len(r_numbers))
        c_pad = c_numbers + (0,) * (max_len - len(c_numbers))
        r_pad = r_numbers + (0,) * (max_len - len(r_numbers))

        if c_pad < r_pad:
            return -1
        if c_pad > r_pad:
            return 1

        if c_suffix and r_suffix and c_suffix != r_suffix:
            return None
        return 0

    _, c_text = version_signature(catalog_version)
    _, r_text = version_signature(release_version)

    if c_text and r_text:
        return 0 if c_text == r_text else None

    return None


def calculate_catalog_status(
    release: dict[str, Any],
    primary: dict[str, Any] | None,
) -> tuple[str, str]:
    latest_version = str(release.get("tag") or release.get("name") or "").strip()

    if not primary:
        return "pending", "No está registrado en PSVitaAlive"

    catalog_version = str(primary.get("version") or "").strip()

    if not catalog_version:
        return "pending", "PSVitaAlive no tiene versión registrada"

    if not latest_version:
        return "pending", f"Catálogo: {catalog_version} · release sin versión comparable"

    comparison = compare_versions(catalog_version, latest_version)

    if comparison in (0, 1):
        if comparison == 1:
            return "uptodate", f"PSVitaAlive {catalog_version} · release {latest_version}"
        return "uptodate", f"PSVitaAlive {catalog_version}"

    return "pending", f"PSVitaAlive {catalog_version} → release {latest_version}"


def clear_catalog_metadata(release: dict[str, Any]) -> None:
    for key in (
        "homebrew_name",
        "homebrew_icon",
        "homebrew_category",
        "homebrew_description",
        "homebrew_long_description",
        "homebrew_id",
        "homebrew_authors",
        "homebrew_version",
        "catalog_apps",
    ):
        release.pop(key, None)


def enrich_release(release: dict[str, Any], apps: list[dict[str, Any]]) -> None:
    if not apps:
        clear_catalog_metadata(release)
        status, detail = calculate_catalog_status(release, None)
        release["catalog_update_status"] = status
        release["catalog_update_detail"] = detail
        release["catalog_version"] = ""
        return

    primary = choose_primary_app(apps, release) or apps[0]
    ordered = [primary] + [app for app in apps if app is not primary]

    release["homebrew_name"] = (
        primary.get("name")
        or release.get("name")
        or release.get("repo")
    )
    release["homebrew_icon"] = primary.get("icon") or ""
    release["homebrew_category"] = primary.get("category") or ""
    release["homebrew_description"] = primary.get("description") or ""
    release["homebrew_long_description"] = primary.get("long_description") or ""
    release["homebrew_id"] = primary.get("id") or ""
    release["homebrew_authors"] = primary.get("author_ids") or []
    release["homebrew_version"] = primary.get("version") or ""
    release["catalog_apps"] = ordered[:12]

    status, detail = calculate_catalog_status(release, primary)
    release["catalog_update_status"] = status
    release["catalog_update_detail"] = detail
    release["catalog_version"] = primary.get("version") or ""


def exact_release_key(item: dict[str, Any]) -> str:
    platform = str(item.get("platform") or "github").lower()
    repo = str(item.get("repo") or "").strip().lower()
    tag = str(item.get("tag") or "").strip().lower()
    published = str(item.get("published") or "").strip()

    if tag:
        return f"{platform}:{repo}:{tag}"

    url = str(item.get("url") or "").strip().lower()
    if url:
        return f"{platform}:{repo}:{url}"

    return f"{platform}:{repo}:{published}"


def merge_release_records(
    preferred: dict[str, Any],
    other: dict[str, Any],
) -> dict[str, Any]:
    """Combina un duplicado conservando la variante con más información."""
    result = dict(preferred)

    for key, value in other.items():
        current = result.get(key)

        if current in (None, "", [], {}):
            result[key] = value
            continue

        if key == "assets" and isinstance(current, list) and isinstance(value, list):
            known = {
                str(asset.get("download_url") or asset.get("name") or "")
                for asset in current
                if isinstance(asset, dict)
            }
            merged = list(current)
            for asset in value:
                if not isinstance(asset, dict):
                    continue
                identity = str(asset.get("download_url") or asset.get("name") or "")
                if identity and identity not in known:
                    known.add(identity)
                    merged.append(asset)
            result[key] = merged

    return result


def dedupe_exact_history(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(
        (item for item in history if isinstance(item, dict)),
        key=lambda item: parse_published(item.get("published")),
        reverse=True,
    )

    unique: dict[str, dict[str, Any]] = {}
    removed = 0

    for item in ordered:
        key = exact_release_key(item)
        if key not in unique:
            unique[key] = item
        else:
            unique[key] = merge_release_records(unique[key], item)
            removed += 1

    return list(unique.values()), removed


def display_identity(item: dict[str, Any]) -> str:
    homebrew_id = str(item.get("homebrew_id") or "").strip().lower()
    if homebrew_id:
        return f"app:{homebrew_id}"

    return repo_key(
        str(item.get("platform") or "github"),
        str(item.get("repo") or ""),
    )


def latest_unique_projects(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Una sola tarjeta por homebrew/repositorio: siempre la más reciente."""
    ordered = sorted(
        (item for item in history if isinstance(item, dict)),
        key=lambda item: parse_published(item.get("published")),
        reverse=True,
    )

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    hidden = 0

    for item in ordered:
        identity = display_identity(item)
        if identity in seen:
            hidden += 1
            continue

        seen.add(identity)
        result.append(item)

    return result, hidden


def format_size(size: int | float | None) -> str:
    if not size:
        return ""

    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024

    return ""


def choose_primary_asset(
    assets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not assets:
        return None

    for extension in (".vpk", ".skprx", ".suprx", ".self", ".zip"):
        for asset in assets:
            if str(asset.get("name") or "").lower().endswith(extension):
                return asset

    return assets[0]


def esc(value: Any, *, quote: bool = False) -> str:
    return html.escape(str(value or ""), quote=quote)


def render_release_body(raw: Any) -> str:
    text = str(raw or "").strip()

    if not text:
        return "<p>Sin descripción.</p>"

    try:
        rendered = MARKDOWN(text)
    except Exception:
        rendered = f"<pre>{esc(text)}</pre>"

    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )

    cleaned = re.sub(
        r"<a(?![^>]*\btarget=)([^>]*)>",
        r'<a target="_blank" rel="noopener noreferrer"\1>',
        cleaned,
        flags=re.IGNORECASE,
    )

    return cleaned


def catalog_badge(item: dict[str, Any]) -> str:
    status = str(item.get("catalog_update_status") or "pending")
    detail = esc(item.get("catalog_update_detail") or "", quote=True)

    if status == "uptodate":
        label = "✓ PSVitaAlive al día"
        css = "catalog-ok"
    elif item.get("homebrew_id"):
        label = "↻ Pendiente PSVitaAlive"
        css = "catalog-pending"
    else:
        label = "＋ Falta en PSVitaAlive"
        css = "catalog-missing"

    return f'<span class="badge {css}" title="{detail}">{label}</span>'


def generate_dashboard(
    history: list[dict[str, Any]],
    sync_info: dict[str, Any],
) -> None:
    display_items, visual_duplicates_hidden = latest_unique_projects(history)
    cards: list[str] = []

    up_to_date_count = sum(
        1
        for item in display_items
        if item.get("catalog_update_status") == "uptodate"
    )
    pending_count = len(display_items) - up_to_date_count

    for item in display_items:
        platform = str(item.get("platform") or "github")
        repo = esc(item.get("repo"))
        release_name = esc(item.get("name") or item.get("tag") or "Release")
        homebrew_name = esc(
            item.get("homebrew_name")
            or item.get("name")
            or item.get("repo")
            or "Homebrew"
        )
        icon = esc(item.get("homebrew_icon"), quote=True)
        category = esc(item.get("homebrew_category") or "homebrew")
        tag = esc(item.get("tag") or "sin tag")
        release_url = esc(item.get("url") or "#", quote=True)
        published = esc(
            str(item.get("published") or "")[:16].replace("T", " ")
        )
        catalog_status = str(item.get("catalog_update_status") or "pending")
        catalog_version = esc(item.get("catalog_version") or "—")

        description_raw = str(
            item.get("body")
            or item.get("homebrew_long_description")
            or item.get("homebrew_description")
            or "Sin descripción"
        ).strip()
        description = render_release_body(description_raw)

        prerelease = (
            '<span class="badge pre">PRE-RELEASE</span>'
            if item.get("prerelease")
            else ""
        )
        psvitaalive_badge = catalog_badge(item)

        apps = (
            item.get("catalog_apps")
            if isinstance(item.get("catalog_apps"), list)
            else []
        )
        extra_apps = [
            app
            for app in apps[1:]
            if isinstance(app, dict) and app.get("name")
        ]

        related = ""
        if extra_apps:
            names = " · ".join(esc(app.get("name")) for app in extra_apps[:6])
            more = len(extra_apps) - 6
            suffix = f" · +{more} más" if more > 0 else ""
            related = (
                f'<div class="related"><span>También asociado:</span> '
                f"{names}{suffix}</div>"
            )

        if icon:
            icon_html = (
                f'<img class="app-icon" src="{icon}" alt="Icono de {homebrew_name}" '
                'loading="lazy" referrerpolicy="no-referrer" '
                "onerror=\"this.parentElement.classList.add('icon-failed');this.remove()\">"
            )
        else:
            icon_html = ""

        assets = (
            item.get("assets")
            if isinstance(item.get("assets"), list)
            else []
        )
        primary_asset = choose_primary_asset(assets)
        buttons: list[str] = []

        if primary_asset:
            url = esc(primary_asset.get("download_url") or "#", quote=True)
            name = esc(primary_asset.get("name") or "Descargar")
            size = format_size(primary_asset.get("size"))
            size_text = f" · {esc(size)}" if size else ""
            buttons.append(
                f'<a class="btn primary" href="{url}" target="_blank" '
                f'rel="noopener">⬇ {name}{size_text}</a>'
            )

        for asset in assets:
            if asset is primary_asset:
                continue

            url = esc(asset.get("download_url") or "#", quote=True)
            name = esc(asset.get("name") or "archivo")
            size = format_size(asset.get("size"))
            size_text = f" · {esc(size)}" if size else ""

            buttons.append(
                f'<a class="btn" href="{url}" target="_blank" rel="noopener">'
                f"{name}{size_text}</a>"
            )

        if not buttons:
            buttons.append(
                '<span class="no-assets">Sin assets publicados</span>'
            )

        catalog_detail = esc(
            item.get("catalog_update_detail") or "",
            quote=True,
        )

        search_text = esc(
            " ".join(
                [
                    str(item.get("homebrew_name") or ""),
                    " ".join(
                        str(app.get("name") or "")
                        for app in apps
                        if isinstance(app, dict)
                    ),
                    str(item.get("repo") or ""),
                    str(item.get("name") or ""),
                    str(item.get("tag") or ""),
                    str(item.get("homebrew_category") or ""),
                    str(item.get("catalog_version") or ""),
                    description_raw,
                ]
            ).lower(),
            quote=True,
        )

        cards.append(
            f"""
            <article
              class="card {platform}"
              data-platform="{platform}"
              data-catalog-status="{catalog_status}"
              data-search="{search_text}">
              <div class="card-layout">
                <div class="icon-wrap">
                  {icon_html}
                  <span class="icon-fallback">PSV</span>
                </div>
                <div class="card-content">
                  <div class="card-top">
                    <div>
                      <div class="eyebrow">{category.upper()} · {platform.upper()} · {repo}</div>
                      <h2><a href="{release_url}" target="_blank" rel="noopener">{homebrew_name}</a></h2>
                      <div class="release-name">{release_name}</div>
                    </div>
                    <div class="badges">{prerelease}{psvitaalive_badge}</div>
                  </div>

                  <div class="meta">
                    <span class="tag">{tag}</span>
                    <span>{published or "fecha desconocida"} UTC</span>
                    <span class="catalog-version" title="{catalog_detail}">
                      PSVitaAlive: {catalog_version}
                    </span>
                  </div>

                  {related}
                  <div class="body markdown-body">{description}</div>
                  <div class="assets">{''.join(buttons)}</div>
                </div>
              </div>
            </article>
            """
        )

    cards_html = (
        "\n".join(cards)
        if cards
        else '<div class="empty">Aún no hay releases registrados.</div>'
    )

    repos = int(sync_info.get("repositories", 0))
    github_count = int(sync_info.get("github", 0))
    gitlab_count = int(sync_info.get("gitlab", 0))
    successful_checks = int(sync_info.get("successful_checks", 0))
    failed_checks = int(sync_info.get("failed_checks", 0))
    updated = esc(
        str(sync_info.get("synced_at") or "")[:16].replace("T", " ")
    )

    sync_info["catalog_up_to_date"] = up_to_date_count
    sync_info["catalog_pending"] = pending_count
    sync_info["dashboard_projects"] = len(display_items)
    sync_info["visual_duplicates_hidden"] = visual_duplicates_hidden

    document = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta
    name="description"
    content="Últimos releases de homebrew y ports de PS Vita, comparados con el catálogo de PSVitaAlive.">
  <title>PS Vita · New Releases</title>

  <style>
    :root {{
      color-scheme: dark;
      --bg:#090d16;
      --panel:#111827;
      --panel2:#0f172a;
      --line:#253047;
      --text:#eef2ff;
      --muted:#94a3b8;
      --accent:#8b5cf6;
      --accent2:#22d3ee;
      --github:#60a5fa;
      --gitlab:#fb923c;
      --green:#34d399;
      --yellow:#fbbf24;
      --orange:#fb923c;
      --red:#f87171;
    }}

    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}

    body {{
      margin:0;
      min-height:100vh;
      font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:
        radial-gradient(circle at top left,rgba(139,92,246,.16),transparent 36rem),
        radial-gradient(circle at top right,rgba(34,211,238,.10),transparent 32rem),
        var(--bg);
      color:var(--text);
    }}

    a {{ color:inherit; }}
    .wrap {{ width:min(1180px,calc(100% - 32px)); margin:0 auto; }}

    header {{ padding:58px 0 28px; }}

    .hero {{
      border:1px solid var(--line);
      background:linear-gradient(145deg,rgba(17,24,39,.94),rgba(15,23,42,.88));
      border-radius:26px;
      padding:30px;
      box-shadow:0 24px 80px rgba(0,0,0,.28);
    }}

    .kicker {{
      color:var(--accent2);
      font-weight:800;
      letter-spacing:.14em;
      font-size:.76rem;
      text-transform:uppercase;
    }}

    h1 {{
      margin:8px 0;
      font-size:clamp(2rem,5vw,3.8rem);
      line-height:1;
      letter-spacing:-.045em;
    }}

    .lead {{
      margin:0;
      color:var(--muted);
      max-width:840px;
      font-size:1.02rem;
    }}

    .stats {{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
      gap:12px;
      margin-top:24px;
    }}

    .stat {{
      border:1px solid var(--line);
      background:rgba(9,13,22,.48);
      border-radius:16px;
      padding:14px;
    }}

    .stat strong {{
      display:block;
      font-size:1.3rem;
    }}

    .stat span {{
      color:var(--muted);
      font-size:.78rem;
    }}

    .stat.ok strong {{ color:#a7f3d0; }}
    .stat.pending strong {{ color:#fdba74; }}

    .toolbar {{
      position:sticky;
      top:0;
      z-index:10;
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      padding:14px 0;
      background:rgba(9,13,22,.86);
      backdrop-filter:blur(14px);
    }}

    input,select {{
      min-height:44px;
      border:1px solid var(--line);
      border-radius:12px;
      background:var(--panel2);
      color:var(--text);
      padding:0 13px;
      outline:none;
    }}

    input {{ flex:1 1 300px; }}

    input:focus,select:focus {{
      border-color:var(--accent);
      box-shadow:0 0 0 3px rgba(139,92,246,.14);
    }}

    #count {{
      margin-left:auto;
      align-self:center;
      color:var(--muted);
      font-size:.86rem;
    }}

    main {{ padding:4px 0 64px; }}

    .card {{
      border:1px solid var(--line);
      border-left:4px solid var(--github);
      background:rgba(17,24,39,.88);
      border-radius:18px;
      padding:20px;
      margin:14px 0;
      box-shadow:0 10px 35px rgba(0,0,0,.13);
    }}

    .card.gitlab {{ border-left-color:var(--gitlab); }}

    .card-layout {{
      display:grid;
      grid-template-columns:92px minmax(0,1fr);
      gap:18px;
    }}

    .icon-wrap {{
      width:92px;
      height:92px;
      border:1px solid var(--line);
      border-radius:20px;
      background:linear-gradient(145deg,#182033,#0d1320);
      overflow:hidden;
      display:grid;
      place-items:center;
      position:relative;
    }}

    .app-icon {{
      width:100%;
      height:100%;
      object-fit:cover;
      position:relative;
      z-index:2;
    }}

    .icon-fallback {{
      position:absolute;
      font-weight:900;
      letter-spacing:.08em;
      color:#64748b;
    }}

    .card-top {{
      display:flex;
      justify-content:space-between;
      gap:16px;
      align-items:flex-start;
    }}

    .eyebrow {{
      color:var(--muted);
      font-size:.72rem;
      font-weight:750;
      letter-spacing:.045em;
    }}

    h2 {{
      margin:4px 0 0;
      font-size:1.45rem;
    }}

    h2 a {{ text-decoration:none; }}
    h2 a:hover {{ color:var(--accent2); }}

    .release-name {{
      color:#c4b5fd;
      font-size:.88rem;
      margin-top:3px;
    }}

    .badges {{
      display:flex;
      flex-wrap:wrap;
      justify-content:flex-end;
      gap:6px;
    }}

    .badge {{
      white-space:nowrap;
      padding:6px 9px;
      border-radius:999px;
      font-size:.66rem;
      font-weight:900;
      letter-spacing:.03em;
      border:1px solid transparent;
    }}

    .badge.pre {{
      color:#111827;
      background:var(--yellow);
    }}

    .badge.catalog-ok {{
      color:#a7f3d0;
      background:rgba(52,211,153,.12);
      border-color:rgba(52,211,153,.35);
    }}

    .badge.catalog-pending {{
      color:#fed7aa;
      background:rgba(251,146,60,.12);
      border-color:rgba(251,146,60,.35);
    }}

    .badge.catalog-missing {{
      color:#fecaca;
      background:rgba(248,113,113,.11);
      border-color:rgba(248,113,113,.32);
    }}

    .meta {{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      margin:10px 0;
      color:var(--muted);
      font-size:.8rem;
      align-items:center;
    }}

    .tag {{
      color:#ddd6fe;
      background:rgba(139,92,246,.13);
      border:1px solid rgba(139,92,246,.28);
      padding:2px 7px;
      border-radius:7px;
    }}

    .catalog-version {{
      color:#bfdbfe;
      background:rgba(96,165,250,.08);
      border:1px solid rgba(96,165,250,.18);
      padding:2px 7px;
      border-radius:7px;
    }}

    .related {{
      color:#94a3b8;
      font-size:.78rem;
      margin:4px 0 10px;
    }}

    .related span {{
      color:#67e8f9;
      font-weight:700;
    }}

    .body {{
      color:#cbd5e1;
      font-size:.91rem;
      line-height:1.55;
      max-height:300px;
      overflow:auto;
      padding:4px 10px 4px 0;
      overflow-wrap:anywhere;
    }}

    .markdown-body > :first-child {{ margin-top:0; }}
    .markdown-body > :last-child {{ margin-bottom:0; }}
    .markdown-body p {{ margin:.55em 0; }}
    .markdown-body h1,
    .markdown-body h2,
    .markdown-body h3,
    .markdown-body h4,
    .markdown-body h5,
    .markdown-body h6 {{
      line-height:1.25;
      margin:1em 0 .45em;
      color:#f8fafc;
    }}

    .markdown-body h1 {{ font-size:1.5rem; }}
    .markdown-body h2 {{ font-size:1.28rem; }}
    .markdown-body h3 {{ font-size:1.12rem; }}
    .markdown-body h4,
    .markdown-body h5,
    .markdown-body h6 {{ font-size:1rem; }}

    .markdown-body ul,
    .markdown-body ol {{
      margin:.5em 0 .7em;
      padding-left:1.5rem;
    }}

    .markdown-body li {{ margin:.2em 0; }}

    .markdown-body a {{
      color:#7dd3fc;
      text-decoration:underline;
      text-underline-offset:2px;
    }}

    .markdown-body img {{
      display:block;
      max-width:min(100%,850px);
      height:auto;
      margin:10px auto;
      border-radius:10px;
    }}

    .markdown-body blockquote {{
      margin:.7em 0;
      padding:.25em .9em;
      border-left:3px solid #475569;
      color:#94a3b8;
      background:rgba(15,23,42,.55);
    }}

    .markdown-body code {{
      background:#0b1220;
      border:1px solid #273449;
      border-radius:5px;
      padding:.08em .35em;
      color:#e2e8f0;
    }}

    .markdown-body pre {{
      overflow:auto;
      background:#0b1220;
      border:1px solid #273449;
      border-radius:10px;
      padding:12px;
    }}

    .markdown-body pre code {{
      border:0;
      padding:0;
      background:transparent;
    }}

    .markdown-body table {{
      width:max-content;
      max-width:100%;
      border-collapse:collapse;
      display:block;
      overflow:auto;
      margin:.8em 0;
    }}

    .markdown-body th,
    .markdown-body td {{
      border:1px solid #334155;
      padding:6px 9px;
      text-align:left;
    }}

    .markdown-body th {{
      background:#172033;
      color:#f8fafc;
    }}

    .markdown-body hr {{
      border:0;
      border-top:1px solid #334155;
      margin:1em 0;
    }}

    .markdown-body input[type="checkbox"] {{
      margin-right:.4em;
      pointer-events:none;
    }}

    .assets {{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin-top:16px;
    }}

    .btn {{
      text-decoration:none;
      border:1px solid var(--line);
      background:#172033;
      color:#dbeafe;
      padding:8px 11px;
      border-radius:10px;
      font-size:.78rem;
      transition:.16s ease;
    }}

    .btn:hover {{
      transform:translateY(-1px);
      border-color:#475569;
    }}

    .btn.primary {{
      border-color:rgba(52,211,153,.38);
      background:rgba(52,211,153,.12);
      color:#a7f3d0;
      font-weight:800;
    }}

    .no-assets,.empty {{ color:var(--muted); }}

    footer {{
      color:var(--muted);
      text-align:center;
      padding:0 0 40px;
      font-size:.8rem;
    }}

    @media (max-width:820px) {{
      #count {{
        width:100%;
        margin-left:0;
      }}
    }}

    @media (max-width:600px) {{
      .wrap {{
        width:min(100% - 20px,1180px);
      }}

      header {{ padding-top:22px; }}

      .hero {{
        padding:20px;
        border-radius:20px;
      }}

      .card {{ padding:15px; }}

      .card-layout {{
        grid-template-columns:64px minmax(0,1fr);
        gap:12px;
      }}

      .icon-wrap {{
        width:64px;
        height:64px;
        border-radius:15px;
      }}

      .card-top {{
        flex-direction:column;
      }}

      .badges {{
        justify-content:flex-start;
      }}

      h2 {{ font-size:1.18rem; }}

      .toolbar select {{
        flex:1 1 150px;
      }}
    }}
  </style>
</head>

<body>
  <header class="wrap">
    <section class="hero">
      <div class="kicker">PSVitaAlive · Release Tracker</div>
      <h1>PS Vita New Releases</h1>
      <p class="lead">
        Último release de cada proyecto, comparado automáticamente con la versión
        disponible en PSVitaAlive. Usa el filtro para ver qué entradas del catálogo
        ya están al día y cuáles requieren actualización.
      </p>

      <div class="stats">
        <div class="stat">
          <strong>{len(display_items)}</strong>
          <span>proyectos visibles</span>
        </div>
        <div class="stat">
          <strong>{repos}</strong>
          <span>repos monitorizados</span>
        </div>
        <div class="stat">
          <strong>{github_count}</strong>
          <span>GitHub</span>
        </div>
        <div class="stat">
          <strong>{gitlab_count}</strong>
          <span>GitLab</span>
        </div>
        <div class="stat ok">
          <strong>{up_to_date_count}</strong>
          <span>PSVitaAlive al día</span>
        </div>
        <div class="stat pending">
          <strong>{pending_count}</strong>
          <span>pendientes</span>
        </div>
        <div class="stat">
          <strong>{successful_checks}</strong>
          <span>consultas correctas</span>
        </div>
        <div class="stat">
          <strong>{failed_checks}</strong>
          <span>errores temporales</span>
        </div>
      </div>
    </section>
  </header>

  <div class="wrap toolbar">
    <input
      id="search"
      type="search"
      placeholder="Buscar homebrew, repositorio, versión o categoría…"
      autocomplete="off">

    <select id="platform">
      <option value="all">Todas las plataformas</option>
      <option value="github">GitHub</option>
      <option value="gitlab">GitLab</option>
    </select>

    <select id="catalogStatus">
      <option value="all">Todo PSVitaAlive</option>
      <option value="uptodate">✓ Al día en PSVitaAlive</option>
      <option value="pending">↻ Pendientes por actualizar</option>
    </select>

    <span id="count"></span>
  </div>

  <main class="wrap" id="releases">
    {cards_html}
  </main>

  <footer class="wrap">
    Última sincronización del catálogo: {updated or 'N/A'} UTC ·
    Se ocultan automáticamente {visual_duplicates_hidden} versiones/repetidos antiguos.
  </footer>

  <script>
    const search = document.getElementById('search');
    const platform = document.getElementById('platform');
    const catalogStatus = document.getElementById('catalogStatus');
    const cards = [...document.querySelectorAll('.card')];
    const count = document.getElementById('count');

    function filterCards() {{
      const q = search.value.trim().toLowerCase();
      const p = platform.value;
      const s = catalogStatus.value;
      let visible = 0;

      for (const card of cards) {{
        const matchesText = !q || card.dataset.search.includes(q);
        const matchesPlatform = p === 'all' || card.dataset.platform === p;
        const matchesCatalog =
          s === 'all' || card.dataset.catalogStatus === s;

        const show = matchesText && matchesPlatform && matchesCatalog;
        card.hidden = !show;

        if (show) visible++;
      }}

      count.textContent = `${{visible}} de ${{cards.length}} proyectos`;
    }}

    search.addEventListener('input', filterCards);
    platform.addEventListener('change', filterCards);
    catalogStatus.addEventListener('change', filterCards);
    filterCards();
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

    response = requests.get(
        CATALOG_URL,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    catalog = response.json()
    index = catalog_repo_index(catalog)

    history, exact_duplicates_removed = dedupe_exact_history(history)

    matched = 0
    with_icon = 0
    up_to_date = 0
    pending = 0

    for release in history:
        key = repo_key(
            str(release.get("platform") or "github"),
            str(release.get("repo") or ""),
        )
        apps = index.get(key, [])

        enrich_release(release, apps)

        if apps:
            matched += 1
            if release.get("homebrew_icon"):
                with_icon += 1

        if release.get("catalog_update_status") == "uptodate":
            up_to_date += 1
        else:
            pending += 1

    history.sort(
        key=lambda item: parse_published(item.get("published")),
        reverse=True,
    )

    save_json(HISTORY_FILE, history)

    sync_info["catalog_metadata_matches"] = matched
    sync_info["catalog_icons"] = with_icon
    sync_info["catalog_up_to_date_history"] = up_to_date
    sync_info["catalog_pending_history"] = pending
    sync_info["exact_duplicates_removed"] = exact_duplicates_removed
    sync_info["metadata_enriched_at"] = datetime.now(timezone.utc).isoformat()

    generate_dashboard(history, sync_info)
    save_json(SYNC_FILE, sync_info)

    display_items, hidden_old_versions = latest_unique_projects(history)

    print(
        "🎨 PSVitaAlive: "
        f"{matched}/{len(history)} releases asociados; "
        f"{with_icon} con icono; "
        f"{sum(1 for item in display_items if item.get('catalog_update_status') == 'uptodate')} al día; "
        f"{sum(1 for item in display_items if item.get('catalog_update_status') != 'uptodate')} pendientes; "
        f"{exact_duplicates_removed} duplicados exactos eliminados; "
        f"{hidden_old_versions} versiones/repetidos antiguos ocultos."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
