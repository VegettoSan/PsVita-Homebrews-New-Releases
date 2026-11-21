#!/usr/bin/env python3
"""Render independent PSVitaAlive discoveries page; never changes the release cards."""
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SOURCE = Path("data/discovery/candidates.json")
STATE = Path("data/discovery/state.json")
OUTPUT = Path("docs/discoveries.html")
DATA = Path("docs/discoveries.json")
AUTHORS = Path("docs/discovery-authors")
GENERATE_URL = "https://vegettosan.github.io/PSVitaAlive/tools/app-generator/"


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def href(value):
    if not isinstance(value, str):
        return ""
    try:
        url = urlparse(value)
        if url.scheme == "https" and not url.username and not url.password and url.hostname in (
            "github.com", "gitlab.com", "raw.githubusercontent.com", "avatars.githubusercontent.com",
            "www.github.com", "www.gitlab.com"):
            return esc(value)
    except ValueError:
        pass
    return ""


def author_identity(entry):
    """Group by the repository account, not an unverified claimed developer."""
    platform = str(entry.get("platform") or "").lower()
    if platform not in ("github", "gitlab"):
        return "", ""
    host = "github.com" if platform == "github" else "gitlab.com"
    raw = str(entry.get("author_profile") or "")
    try:
        url = urlparse(raw)
        parts = [x for x in url.path.split("/") if x]
        if (url.scheme == "https" and url.hostname == host and
                not url.username and not url.password and not url.port and
                len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", parts[0])):
            handle = parts[0]
            return platform + ":" + handle.casefold(), handle
    except ValueError:
        pass
    # Keep older discoveries usable if author_profile was not stored.
    project = str(entry.get("project") or "")
    handle = project.split("/", 1)[0] if "/" in project else ""
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", handle):
        return platform + ":" + handle.casefold(), handle
    return "", ""


def author_options(entries):
    """Return safe static options with per-account discovery counts."""
    counts = {}
    names = {}
    for entry in entries:
        key, handle = author_identity(entry)
        if key:
            counts[key] = counts.get(key, 0) + 1
            names.setdefault(key, handle)
    options = sorted(counts, key=lambda key: (-counts[key], names[key].casefold(), key))
    return "".join(
        '<option value="' + esc(key) + '">' + esc(names[key]) +
        ' · ' + ("GitHub" if key.startswith("github:") else "GitLab") +
        ' (' + str(counts[key]) + ')</option>'
        for key in options
    )


def card(entry):
    app = entry.get("app_draft") or {}
    key = str(entry.get("key") or "").replace(":", "_")
    if not re.fullmatch(r"(gh|gl)_[0-9]+", key):
        return ""
    repo_link = href(entry.get("source_url"))
    author_link = href(entry.get("author_profile"))
    icon_link = href(app.get("icon"))
    title = esc(app.get("name") or entry.get("project"))
    desc = esc(app.get("description"))
    images = [href(x) for x in app.get("screenshots", []) if href(x)][:5]
    missing = ", ".join(str(x).replace("_", " ") for x in entry.get("missing_fields", []))
    score = int(entry.get("score") or 0)
    evidence = ", ".join(str(x) for x in entry.get("evidence", [])[:6])
    project_type = {"patch": "Parche", "plugin": "Plugin", "homebrew": "Homebrew / port"}.get(
        entry.get("project_type"), "Homebrew / port")
    destination = str(entry.get("target_reason") or "Posible proyecto para PS Vita")
    author_draft = entry.get("author_draft")
    author_download = ('<a href="discovery-authors/' + key + '.json" download>JSON de autor</a>'
                       if isinstance(author_draft, dict) else "")
    poster = ('<img class="app-icon" loading="lazy" alt="" src="' + icon_link + '">'
              if icon_link else '<div class="app-icon placeholder" aria-hidden="true">PSV</div>')
    previews = "".join('<a href="' + x + '" target="_blank" rel="noopener noreferrer">'
                       '<img loading="lazy" alt="Captura del proyecto" src="' + x + '"></a>' for x in images)
    author_key, author_name = author_identity(entry)
    searchable = esc((str(app.get("name") or "") + " " + str(entry.get("project") or "") +
                      " " + str(app.get("description") or "") +
                      " " + author_name).lower())
    platform = esc(entry.get("platform"))
    missing_note = ('<p class="muted"><strong>Faltan:</strong> ' + esc(missing) + '</p>') if missing else ""
    return ('<article class="entry" data-search="' + searchable + '" data-platform="' + platform + '" '
            'data-media="' + ("yes" if icon_link or images else "no") +
            '" data-author="' + esc(author_key) + '">'
            '<div class="top">' + poster + '<div><h2>' + title + '</h2>'
            '<p class="muted">' + esc(entry.get("project")) + '</p>' +
            (('<p class="muted">Autor / cuenta: <strong>' + esc(author_name) + '</strong></p>')
             if author_name else '') +
            '<p class="desc">' + desc + '</p></div></div>'
            '<div class="tags"><span>' + platform.upper() + '</span><span>' + esc(project_type) +
            '</span><span>Pendiente de revisión</span></div>'
            '<details><summary>Ver ficha y datos encontrados</summary>'
            '<p class="muted"><strong>Destino:</strong> ' + esc(destination) + '</p>'
            '<p class="muted"><strong>Indicios:</strong> ' + esc(evidence) + '</p>' +
            (('<p class="long-desc">' + esc(app.get("long_description")) + '</p>') if app.get("long_description") else "") +
            missing_note + (('<div class="shots">' + previews + '</div>') if images else '') +
            '<p class="muted">Descubierto: ' + esc(str(entry.get("discovered_at") or "")[:10]) + '</p></details>'
            '<div class="links"><a href="' + repo_link + '" target="_blank" rel="noopener noreferrer">Repositorio</a>' +
            (('<a href="' + author_link + '" target="_blank" rel="noopener noreferrer">Autor / perfil</a>')
             if author_link else '') +
            (('<button type="button" class="filter-author" data-select-author="' +
              esc(author_key) + '">Ver proyectos del autor</button>') if author_key else '') +
            '<a href="discovery-drafts/' + key + '.json" download>JSON de aplicación</a>' +
            author_download +
            '<a href="' + GENERATE_URL + '" target="_blank" rel="noopener noreferrer">Abrir generador</a></div></article>')


def render(entries, state):
    entries = sorted((v for v in entries.values() if isinstance(v, dict)),
                     key=lambda x: x.get("discovered_at") or "", reverse=True)
    cards = "".join(card(item) for item in entries)
    choices = author_options(entries)
    count = len(entries)
    last = state.get("last_round") or {}
    scanned = int(last.get("repositories_checked") or 0)
    queued = int(last.get("pending") or 0)
    processed = len(state.get("seen") or {})
    new_found = int(last.get("new_candidates") or 0)
    catalogued = int(last.get("catalogued_removed") or 0)
    page = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Nuevos descubrimientos — PSVitaAlive</title>
<style>
:root{--bg:#000;--surface:#373737;--border:#6e6e6e;--text:#ededed;--muted:#b8b8b8;--accent:#3bff00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
.wrap{width:min(1200px,calc(100% - 32px));margin:auto}header{padding:26px 0 12px}h1{margin:8px 0 4px;font-size:clamp(1.6rem,3vw,2.2rem)}
nav{display:flex;flex-wrap:wrap;gap:9px;padding:10px 0 22px}nav a{padding:11px 15px;border:1px solid var(--border);background:#191919;border-radius:11px;color:var(--text);text-decoration:none;font-weight:700}
nav a.active{background:var(--accent);border-color:var(--accent);color:#000}
p{margin:8px 0}.muted{color:var(--muted)}.summary{padding:18px;border:1px solid var(--border);background:#232323;border-radius:12px;margin-bottom:15px}
.counts{display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 0}.counts strong{color:var(--accent)}
.filters{display:flex;gap:9px;flex-wrap:wrap;margin:15px 0}input,select{font:inherit;color:#fff;background:#202020;border:1px solid var(--border);border-radius:9px;padding:12px;min-width:160px}input{flex:1 1 280px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding-bottom:50px}
.entry{min-width:0;border:1px solid var(--border);border-left:4px solid var(--accent);background:var(--surface);border-radius:12px;padding:16px}
.top{display:flex;gap:13px;align-items:flex-start}.app-icon{width:72px;height:72px;object-fit:cover;flex:0 0 72px;border:1px solid var(--border);border-radius:10px;background:#111}
.placeholder{display:flex;align-items:center;justify-content:center;font-weight:900;color:var(--accent)}
h2{font-size:1.08rem;margin:0;overflow-wrap:anywhere}.desc{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.tags,.links{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.tags span{font-size:.72rem;color:#fff;background:#555;padding:3px 8px;border-radius:6px}
.links a,.links button{font:inherit;color:#fff;border:1px solid var(--border);padding:7px 10px;background:#1e1e1e;border-radius:8px;text-decoration:none;font-size:.82rem;cursor:pointer}
.links a:hover,.links button:hover{color:var(--accent);border-color:var(--accent)}
.filter-author{font-weight:700!important}
.results-meta{color:var(--muted);margin:0 0 15px}
.filters label{display:flex;align-items:center;gap:8px;font-weight:600;color:var(--muted)}
.filters label select{min-width:220px;max-width:100%}
@media(max-width:600px){.filters label{width:100%;justify-content:space-between}.filters label select{flex:1;min-width:0}}

details{margin:12px 0;border-top:1px solid var(--border);padding-top:11px}summary{cursor:pointer;font-weight:700}
.shots{display:flex;gap:9px;overflow-x:auto;padding:9px 0}.shots img{height:100px;width:auto;max-width:190px;object-fit:cover;border-radius:7px}
.long-desc{white-space:normal;overflow-wrap:anywhere}#empty{padding:30px;text-align:center}
@media(max-width:800px){.grid{grid-template-columns:1fr}}a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
</style></head><body><div class="wrap">
<header><h1>Nuevos descubrimientos</h1><p class="muted">Proyectos encontrados automáticamente; ninguno está verificado ni publicado en el catálogo por este sistema.</p></header>
<nav aria-label="Herramientas"><a href="index.html#releases">Actualizaciones Homebrew</a><a href="index.html#game-data">Búsqueda de Game Data</a><a href="discoveries.html" class="active" aria-current="page">Nuevos descubrimientos</a></nav>
<section class="summary"><strong>Exploración incremental</strong><p class="muted">Cada ejecución conserva el progreso y analiza otro lote. Los JSON son borradores incompletos para revisar en el generador de PSVitaAlive.</p>
<div class="counts"><span>Descubrimientos pendientes de publicar: <strong>__COUNT__</strong></span><span>Procesados históricamente: <strong>__PROCESSED__</strong></span><span>Revisados en última ronda: <strong>__SCANNED__</strong></span><span>Nuevos en última ronda: <strong>__NEW_FOUND__</strong></span><span>Retirados por estar en catálogo: <strong>__CATALOGUED__</strong></span><span>En cola: <strong>__QUEUED__</strong></span></div><p class="muted">Solo aparecen los proyectos con indicios de ejecutarse o aplicarse en PS Vita. Los demás se conservan en el historial de búsqueda, sin saturar esta página.</p></section>
<div class="filters" id="filters"><input id="search" type="search" aria-label="Buscar proyectos" placeholder="Buscar por nombre, autor o descripción">
<label for="author">Autor <select id="author" aria-label="Filtrar por autor"><option value="all">Todos los autores</option>__AUTHORS__</select></label>
<select id="platform" aria-label="Filtrar plataforma"><option value="all">GitHub y GitLab</option><option value="github">GitHub</option><option value="gitlab">GitLab</option></select>
<select id="media" aria-label="Filtrar recursos"><option value="all">Con y sin imágenes</option><option value="yes">Con imágenes</option><option value="no">Sin imágenes</option></select></div>
<p class="muted">La agrupación por autor utiliza la cuenta que publica el repositorio en GitHub o GitLab; la autoría definitiva se verifica al registrar el Homebrew.</p>
<p id="visible-count" class="results-meta" aria-live="polite"></p>
<main class="grid" id="cards">__CARDS__</main><p id="empty" hidden>No hay resultados para estos filtros.</p>
</div><script>
(function(){
const search=document.getElementById('search'),author=document.getElementById('author'),platform=document.getElementById('platform'),media=document.getElementById('media');
const cards=Array.from(document.querySelectorAll('.entry')),empty=document.getElementById('empty'),visible=document.getElementById('visible-count');
const start=new URLSearchParams(window.location.search).get('autor');
if(start && Array.from(author.options).some(function(option){return option.value===start;})){author.value=start;}
function apply(){
const q=search.value.trim().toLowerCase();let shown=0;
cards.forEach(function(card){
const match=(!q||card.dataset.search.includes(q))&&(author.value==='all'||card.dataset.author===author.value)&&(platform.value==='all'||card.dataset.platform===platform.value)&&(media.value==='all'||card.dataset.media===media.value);
card.hidden=!match;if(match)shown++;
});
visible.textContent='Mostrando '+shown+' de '+cards.length+' descubrimientos';
empty.hidden=shown!==0;
}
function selectAuthor(value){
if(!Array.from(author.options).some(function(option){return option.value===value;}))return;
author.value=value;
const url=new URL(window.location.href);
if(value==='all')url.searchParams.delete('autor');else url.searchParams.set('autor',value);
window.history.replaceState(null,'',url.pathname+url.search+url.hash);
apply();
}
document.getElementById('cards').addEventListener('click',function(event){
const button=event.target.closest('button[data-select-author]');
if(!button)return;
selectAuthor(button.dataset.selectAuthor);
document.getElementById('filters').scrollIntoView({behavior:'smooth',block:'start'});
});
search.addEventListener('input',apply);
author.addEventListener('change',function(){selectAuthor(author.value);});
platform.addEventListener('change',apply);media.addEventListener('change',apply);apply();
}());
</script></body></html>"""
    return (page.replace("__COUNT__", str(count)).replace("__SCANNED__", str(scanned))
            .replace("__PROCESSED__", str(processed)).replace("__NEW_FOUND__", str(new_found))
            .replace("__CATALOGUED__", str(catalogued))
            .replace("__QUEUED__", str(queued)).replace("__AUTHORS__", choices)
            .replace("__CARDS__", cards))


def main():
    candidates = json.loads(SOURCE.read_text(encoding="utf-8")) if SOURCE.exists() else {}
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if not isinstance(candidates, dict) or not isinstance(state, dict):
        raise ValueError("Discovery files invalid")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Old drafts for rejected/review-only projects must disappear from public Pages.
    # These two directories contain generated files, never manual work.
    for folder in (Path("docs/discovery-drafts"), AUTHORS):
        if folder.exists():
            for file in folder.glob("*.json"):
                if re.fullmatch(r"(gh|gl)_[0-9]+", file.stem) and file.stem.replace("_", ":", 1) not in candidates:
                    file.unlink()
    OUTPUT.write_text(render(candidates, state), encoding="utf-8")
    DATA.write_text(json.dumps(list(candidates.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    AUTHORS.mkdir(parents=True, exist_ok=True)
    for key, entry in candidates.items():
        slug = key.replace(":", "_")
        if re.fullmatch(r"(gh|gl)_[0-9]+", slug) and isinstance(entry.get("author_draft"), dict):
            (AUTHORS / (slug + ".json")).write_text(
                json.dumps(entry["author_draft"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Discovery dashboard generated: " + str(len(candidates)) + " candidates")


if __name__ == "__main__":
    main()
