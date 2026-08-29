#!/usr/bin/env python3
"""
PsVita Homebrews New Releases Monitor
Soporta GitHub + GitLab.
Guarda historial completo + genera dashboard HTML.
Notifica a Discord solo cuando hay releases nuevos.
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from html import escape

# ==================== CONFIG ====================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")

REPOS_FILE = "repos.txt"
STATE_FILE = "data/state.json"
HISTORY_FILE = "data/releases.json"
HTML_FILE = "docs/index.html"

MAX_HISTORY = 100  # máximo de releases guardados en el historial
# ================================================


def load_repos():
    repos = []
    if not Path(REPOS_FILE).exists():
        print(f"❌ No existe {REPOS_FILE}")
        return []

    with open(REPOS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            platform = "gitlab" if "gitlab.com" in line.lower() else "github"
            line = line.rstrip("/")

            if "://" in line:
                parts = [p for p in line.split("/") if p]
                owner = parts[-2]
                repo = parts[-1]
            else:
                owner, repo = line.split("/", 1)

            repos.append({
                "platform": platform,
                "owner": owner,
                "repo": repo,
                "full": f"{owner}/{repo}"
            })

    # únicos
    seen = set()
    unique = []
    for r in repos:
        key = f"{r['platform']}:{r['full']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def load_json(path, default=None):
    if default is None:
        default = {}
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_github_release(owner, repo):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()

        assets = []
        for a in data.get("assets", []):
            assets.append({
                "name": a.get("name"),
                "size": a.get("size", 0),
                "download_url": a.get("browser_download_url"),
                "content_type": a.get("content_type", "")
            })

        return {
            "platform": "github",
            "repo": f"{owner}/{repo}",
            "tag": data.get("tag_name"),
            "name": data.get("name") or data.get("tag_name"),
            "url": data.get("html_url"),
            "published": data.get("published_at"),
            "prerelease": data.get("prerelease", False),
            "draft": data.get("draft", False),
            "body": data.get("body") or "",
            "assets": assets,
            "author": data.get("author", {}).get("login", "")
        }
    except Exception as e:
        print(f"  ⚠️  Error GitHub {owner}/{repo}: {e}")
        return None


def get_gitlab_release(owner, repo):
    headers = {}
    if GITLAB_TOKEN:
        headers["PRIVATE-TOKEN"] = GITLAB_TOKEN

    project = f"{owner}%2F{repo}"
    url = f"https://gitlab.com/api/v4/projects/{project}/releases"

    try:
        r = requests.get(url, headers=headers, timeout=25, params={"per_page": 1})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if not data:
            return None

        latest = data[0]
        assets = []

        # GitLab assets (links)
        links = latest.get("assets", {}).get("links", [])
        for link in links:
            assets.append({
                "name": link.get("name") or link.get("url", "").split("/")[-1],
                "size": 0,
                "download_url": link.get("url"),
                "content_type": ""
            })

        # También sources si existen
        sources = latest.get("assets", {}).get("sources", [])
        for s in sources:
            assets.append({
                "name": f"source.{s.get('format', 'zip')}",
                "size": 0,
                "download_url": s.get("url"),
                "content_type": ""
            })

        tag = latest.get("tag_name")
        return {
            "platform": "gitlab",
            "repo": f"{owner}/{repo}",
            "tag": tag,
            "name": latest.get("name") or tag,
            "url": latest.get("_links", {}).get("self") or f"https://gitlab.com/{owner}/{repo}/-/releases/{tag}",
            "published": latest.get("released_at") or latest.get("created_at"),
            "prerelease": False,
            "draft": False,
            "body": latest.get("description") or "",
            "assets": assets,
            "author": ""
        }
    except Exception as e:
        print(f"  ⚠️  Error GitLab {owner}/{repo}: {e}")
        return None


def format_size(size):
    if not size or size <= 0:
        return ""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def send_discord(release):
    if not DISCORD_WEBHOOK_URL:
        return

    color = 0x5865F2 if release["platform"] == "github" else 0xFC6D26
    pre = " 🟡 Pre-release" if release.get("prerelease") else ""

    # Botones de assets (máximo 5 para no saturar)
    asset_fields = []
    for a in release.get("assets", [])[:5]:
        size_str = f" ({format_size(a['size'])})" if a.get("size") else ""
        asset_fields.append({
            "name": a["name"] + size_str,
            "value": f"[⬇ Descargar]({a['download_url']})",
            "inline": True
        })

    body_preview = release["body"][:350] + ("..." if len(release["body"]) > 350 else "")

    embed = {
        "title": f"🆕 {escape(release['name'])}",
        "url": release["url"],
        "color": color,
        "description": escape(body_preview) if body_preview else "*Sin descripción*",
        "fields": [
            {"name": "Repositorio", "value": f"`{release['repo']}`", "inline": True},
            {"name": "Plataforma", "value": release["platform"].capitalize(), "inline": True},
            {"name": "Tag / Versión", "value": f"`{release['tag']}`", "inline": True},
            {"name": "Fecha", "value": (release["published"] or "")[:10] or "N/A", "inline": True},
        ] + asset_fields,
        "footer": {"text": "PsVita Homebrews New Releases"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    payload = {
        "content": f"**Nuevo release detectado**{pre}",
        "embeds": [embed]
    }

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        print("  ✅ Discord notificado")
    except Exception as e:
        print(f"  ❌ Error Discord: {e}")


def generate_html(history):
    """Genera un dashboard HTML autocontenido y bonito."""

    cards = []
    for item in history:
        assets_html = ""
        for a in item.get("assets", []):
            size = format_size(a.get("size", 0))
            size_badge = f'<span class="size">{size}</span>' if size else ""
            assets_html += f'''
            <a class="btn" href="{escape(a.get('download_url', '#'))}" target="_blank" rel="noopener">
                ⬇ {escape(a.get('name', 'archivo'))} {size_badge}
            </a>'''

        if not assets_html:
            assets_html = '<span class="no-assets">Sin archivos adjuntos</span>'

        platform_class = item.get("platform", "github")
        pre_badge = '<span class="badge pre">Pre-release</span>' if item.get("prerelease") else ""
        body = escape(item.get("body") or "Sin descripción").replace("\n", "<br>")

        published = (item.get("published") or "")[:16].replace("T", " ")

        cards.append(f'''
        <article class="card {platform_class}">
            <div class="card-header">
                <div class="title-row">
                    <h2><a href="{escape(item.get('url', '#'))}" target="_blank" rel="noopener">{escape(item.get('name', item.get('tag', '')))}</a></h2>
                    {pre_badge}
                </div>
                <div class="meta">
                    <span class="repo">{escape(item.get('repo', ''))}</span>
                    <span class="platform">{platform_class.upper()}</span>
                    <span class="tag">{escape(item.get('tag', ''))}</span>
                    <span class="date">{published}</span>
                </div>
            </div>
            <div class="body">{body}</div>
            <div class="assets">
                {assets_html}
            </div>
        </article>''')

    cards_html = "\n".join(cards) if cards else '<p class="empty">Aún no hay releases registrados.</p>'

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PsVita Homebrews – New Releases</title>
<style>
:root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2e3b;
    --text: #e6e9f0;
    --muted: #9aa3b5;
    --accent: #7c5cff;
    --github: #2f81f7;
    --gitlab: #fc6d26;
    --pre: #f0b429;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 24px 16px 60px;
}}
.container {{ max-width: 920px; margin: 0 auto; }}
header {{
    margin-bottom: 32px;
    text-align: center;
}}
header h1 {{
    font-size: 1.8rem;
    font-weight: 700;
    letter-spacing: -0.02em;
}}
header p {{
    color: var(--muted);
    margin-top: 6px;
    font-size: 0.95rem;
}}
.stats {{
    display: flex;
    gap: 16px;
    justify-content: center;
    margin: 20px 0 28px;
    flex-wrap: wrap;
}}
.stat {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 18px;
    font-size: 0.9rem;
}}
.stat strong {{ color: var(--accent); }}

.card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 18px;
    transition: border-color 0.2s;
}}
.card:hover {{ border-color: #3d4455; }}
.card.github {{ border-left: 4px solid var(--github); }}
.card.gitlab {{ border-left: 4px solid var(--gitlab); }}

.card-header {{ margin-bottom: 12px; }}
.title-row {{
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}}
.card h2 {{
    font-size: 1.25rem;
    font-weight: 600;
}}
.card h2 a {{
    color: var(--text);
    text-decoration: none;
}}
.card h2 a:hover {{ color: var(--accent); }}

.badge {{
    font-size: 0.7rem;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 999px;
    text-transform: uppercase;
}}
.badge.pre {{
    background: rgba(240, 180, 41, 0.15);
    color: var(--pre);
}}

.meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px 14px;
    margin-top: 8px;
    font-size: 0.82rem;
    color: var(--muted);
}}
.meta .repo {{ font-family: ui-monospace, monospace; }}
.meta .platform {{
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}
.card.github .platform {{ color: var(--github); }}
.card.gitlab .platform {{ color: var(--gitlab); }}
.meta .tag {{
    background: #252836;
    padding: 2px 8px;
    border-radius: 6px;
    font-family: ui-monospace, monospace;
    color: #c5cae0;
}}

.body {{
    font-size: 0.92rem;
    color: #c8cdd9;
    margin: 12px 0 16px;
    max-height: 180px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
}}
.body::-webkit-scrollbar {{ width: 6px; }}
.body::-webkit-scrollbar-thumb {{ background: #3a3f50; border-radius: 3px; }}

.assets {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}}
.btn {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #252836;
    color: #e0e4f0;
    text-decoration: none;
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 500;
    border: 1px solid #343a4d;
    transition: background 0.15s, border-color 0.15s;
}}
.btn:hover {{
    background: #2f3548;
    border-color: var(--accent);
    color: #fff;
}}
.btn .size {{
    font-size: 0.75rem;
    color: var(--muted);
    margin-left: 4px;
}}
.no-assets {{
    color: var(--muted);
    font-size: 0.85rem;
    font-style: italic;
}}
.empty {{
    text-align: center;
    color: var(--muted);
    padding: 40px 0;
}}
footer {{
    text-align: center;
    margin-top: 40px;
    color: var(--muted);
    font-size: 0.8rem;
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>PsVita Homebrews – New Releases</h1>
        <p>Monitor de releases de homebrew (GitHub + GitLab)</p>
        <div class="stats">
            <div class="stat">Total en historial: <strong>{len(history)}</strong></div>
            <div class="stat">Última actualización: <strong>{now}</strong></div>
        </div>
    </header>

    <main>
        {cards_html}
    </main>

    <footer>
        Generado automáticamente · PsVita-Homebrews-New-Releases
    </footer>
</div>
</body>
</html>'''

    Path(HTML_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📄 Dashboard generado → {HTML_FILE}")


def main():
    repos = load_repos()
    if not repos:
        print("No hay repositorios en repos.txt")
        # Aun así regeneramos el HTML con el historial existente
        history = load_json(HISTORY_FILE, [])
        generate_html(history)
        return

    state = load_json(STATE_FILE, {})
    history = load_json(HISTORY_FILE, [])

    print(f"🔍 Revisando {len(repos)} repositorios...\n")
    new_count = 0

    for item in repos:
        platform = item["platform"]
        owner = item["owner"]
        repo = item["repo"]
        full = item["full"]
        key = f"{platform}:{full}"

        print(f"→ {platform.upper()} | {full}")

        if platform == "github":
            release = get_github_release(owner, repo)
        else:
            release = get_gitlab_release(owner, repo)

        if not release or not release.get("tag"):
            print("  (sin releases)")
            continue

        last_tag = state.get(key)
        current_tag = release["tag"]

        if last_tag != current_tag:
            print(f"  🎉 Nuevo: {current_tag}")
            send_discord(release)

            # Añadir al principio del historial
            history.insert(0, release)
            state[key] = current_tag
            new_count += 1
        else:
            print(f"  ✓ Sin cambios ({current_tag})")

    # Limitar historial
    history = history[:MAX_HISTORY]

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    generate_html(history)

    print(f"\n✅ Listo. Nuevos releases: {new_count}")
    print(f"   Historial: {len(history)} entradas")


if __name__ == "__main__":
    main()
