"""Add an accessible Game Data discovery tab to the existing generated dashboard.

The release cards and their filters remain unchanged. All data in the report is
treated as untrusted text; links lead to public project pages, not binaries.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote_plus, urlparse

REPORT_FILE = Path("data/game_data_candidates.json")

STYLE = """
  /* Independent Game Data panel: same PSVitaAlive colors and spacing. */
  .gd-tabs{display:flex;gap:8px;padding:0 0 9px;flex-wrap:wrap}
  .gd-tab{border:1px solid var(--border);border-radius:var(--radius);
    padding:12px 18px;background:#191919;color:var(--white);
    cursor:pointer;font:inherit;font-weight:700}
  .gd-tab[aria-selected="true"]{background:var(--accent);color:#000;
    border-color:var(--accent)}
  .gd-tab:focus-visible,.gd-input:focus-visible{outline:2px solid var(--accent);
    outline-offset:2px}
  .gd-pill{display:inline-block;margin-left:7px;border-radius:999px;
    padding:1px 7px;background:#454545;color:#fff;font-size:.78rem}
  .gd-tab[aria-selected="true"] .gd-pill{background:#153b0e;color:#fff}
  #gd-panel[hidden],#releases[hidden],.toolbar[hidden],.gd-item[hidden],
  .gd-empty-filter[hidden]{display:none!important}
  #gd-panel{padding:14px 0 64px}
  .gd-summary{background:rgba(55,55,55,.84);border:1px solid var(--border);
    border-radius:var(--radius);padding:20px;margin:0 0 16px}
  .gd-summary h2{margin:0 0 8px}
  .gd-summary p{margin:5px 0;color:var(--text-secondary)}
  .gd-totals{display:flex;flex-wrap:wrap;gap:12px;margin:15px 0 0}
  .gd-total{background:#171717;border:1px solid var(--border);
    border-radius:10px;padding:8px 13px}
  .gd-total strong{display:block;color:var(--accent);font-size:1.3rem}
  .gd-tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:13px}
  .gd-input{flex:1 1 280px}
  .gd-tools select{flex:0 1 250px}
  .gd-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
  .gd-item{border:1px solid var(--border);border-left:4px solid var(--accent);
    background:rgba(55,55,55,.84);border-radius:var(--radius);
    padding:16px;min-width:0}
  .gd-top{display:flex;gap:13px;align-items:flex-start}
  .gd-icon{width:60px;height:60px;object-fit:cover;border-radius:9px;
    border:1px solid var(--border);background:#111;flex-shrink:0}
  .gd-title{color:var(--white);font-size:1.05rem;font-weight:800;
    overflow-wrap:anywhere}
  .gd-id{color:var(--text-secondary);font-size:.76rem;overflow-wrap:anywhere}
  .gd-note{font-size:.81rem;color:var(--text-secondary);margin:10px 0}
  .gd-result{border-top:1px solid var(--border);padding:9px 0}
  .gd-result a{color:var(--accent);text-decoration:underline;
    text-underline-offset:3px;overflow-wrap:anywhere}
  .gd-label{font-size:.73rem;color:var(--text-secondary)}
  .gd-new{color:#000;background:var(--accent);border-radius:5px;
    padding:1px 5px;font-size:.68rem;font-weight:800;margin-left:5px}
  .gd-links{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
  .gd-links a{display:inline-block;background:#191919;border:1px solid var(--border);
    padding:7px 10px;border-radius:9px;color:var(--white);font-size:.76rem}
  .gd-links a:hover{border-color:var(--accent);color:var(--accent)}
  .gd-no-match{font-size:.8rem;color:var(--text-secondary);margin:12px 0}
  .gd-empty-filter{border:1px dashed var(--border);border-radius:10px;
    padding:22px;text-align:center}
  body.gd-active .stats{display:none}
  @media(max-width:800px){.gd-grid{grid-template-columns:1fr}}
  @media(max-width:620px){.gd-tab{flex:1;padding:10px 8px}}
"""


def esc(value):
    return html.escape(str(value or ""), quote=True)


def safe_href(url):
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return ""
    if parsed.hostname not in {
        "github.com", "gitlab.com", "www.reddit.com", "raw.githubusercontent.com",
        "archive.org", "www.archive.org", "i.imgur.com", "docs.google.com"
    }:
        return ""
    return esc(url)


def render_panel(report):
    apps = report.get("apps", [])
    if not isinstance(apps, list):
        apps = []
    matches = [a for a in apps if isinstance(a, dict) and a.get("candidates")]
    with_matches = len(matches)
    official_count = sum(any(c.get("kind") == "possible-archive" for c in a["candidates"])
                         for a in matches)
    community_count = sum(any(c.get("kind") == "community-sheet" for c in a["candidates"])
                          for a in matches)
    timestamp = str(report.get("scanned_at") or "")[:16].replace("T", " ")
    parts = []
    for app in matches:
        name = esc(app.get("name"))
        app_id = esc(app.get("id"))
        title_id = esc(app.get("title_id"))
        reason = esc(app.get("reason"))
        icon = safe_href(app.get("icon"))
        icon_html = ('<img class="gd-icon" alt="" loading="lazy" src="' +
                     icon + '">' if icon else "")
        options = []
        news = set(app.get("new_urls") or [])
        for candidate in (app.get("candidates") or [])[:8]:
            if not isinstance(candidate, dict):
                continue
            url = safe_href(candidate.get("url"))
            if not url:
                continue
            label = esc(candidate.get("title"))
            source = esc(candidate.get("source"))
            date = esc(candidate.get("date"))
            kind = esc(candidate.get("kind"))
            if kind == "community-sheet":
                source += " · Referencia comunitaria, archivo sin verificar"
            else:
                source += " · Archivo identificado en release oficial"
            newly = '<span class="gd-new">Nuevo</span>' if candidate.get("url") in news else ""
            options.append(
                '<div class="gd-result"><div class="gd-label">' + source +
                (" · " + date if date else "") + newly + '</div><a href="' +
                url + '" target="_blank" rel="noopener noreferrer">' +
                label + '</a></div>'
            )
        query = quote_plus(str(app.get("name") or "") + " PS Vita game data")
        reddit = "https://www.reddit.com/search/?q=" + query
        projects = []
        for entry in (app.get("repositories") or [])[:2]:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            platform, project = entry
            if platform not in ("github", "gitlab"):
                continue
            home = "https://" + platform + ".com/" + str(project)
            url = safe_href(home)
            if url:
                projects.append('<a href="' + url +
                                '" target="_blank" rel="noopener noreferrer">Repositorio</a>')
        projects.append('<a href="' + esc(reddit) +
                        '" target="_blank" rel="noopener noreferrer">Buscar en Reddit</a>')
        search_text = esc(" ".join([str(app.get("name") or ""),
                                    str(app.get("title_id") or ""),
                                    str(app.get("id") or "")]).lower())
        parts.append(
            '<article class="gd-item" data-found="' + " ".join(
                label for label, kind in (("official", "possible-archive"),
                                          ("community", "community-sheet"))
                if any(c.get("kind") == kind for c in app["candidates"])
            ) +
            '" data-search="' + search_text + '"><div class="gd-top">' + icon_html +
            '<div><div class="gd-title">' + name + '</div><div class="gd-id">' +
            title_id + ' · ' + app_id + '</div></div></div><p class="gd-note">' +
            reason + '</p>' +
            ("".join(options) if options else '<p class="gd-no-match">Sin pistas detectadas todavía.</p>') +
            '<div class="gd-links">' + "".join(projects) + '</div></article>'
        )
    report_message = (
        "Todavía no se ha ejecutado el nuevo workflow."
        if not timestamp else
        "Última exploración: " + esc(timestamp) + " UTC."
    )
    empty_text = (
        '<p>Por ahora no hay referencias con evidencia suficiente. '
        'Los ports sin resultados permanecen ocultos.</p>'
        if timestamp else '<p>' + report_message + '</p>'
    )
    return (
        '<section class="wrap" id="gd-panel" aria-labelledby="gd-heading" hidden>'
        '<div class="gd-summary"><h2 id="gd-heading">Búsqueda de Game Data</h2>'
        '<p>Solo se muestran ports sin Game/Data Files que tienen evidencia: '
        'un archivo identificado en releases oficiales o una referencia en la '
        'hoja comunitaria enlazada desde Reddit. No equivale a validar '
        'el contenido ni los derechos de distribución.</p>'
        '<p>' + report_message + '</p>'
        '<p><a class="btn" target="_blank" rel="noopener noreferrer" '
        'href="https://docs.google.com/spreadsheets/d/1_nuD1Sr5sNCtZPjWMliR-fFZDvQToUtnMwhh_dJkNeE/edit?gid=1739999294">'
        'Consultar hoja comunitaria</a> '
        '<a class="btn" target="_blank" rel="noopener noreferrer" '
        'href="https://www.reddit.com/r/VitaPiracy/comments/1xd0dai/full_list_of_ps_vita_ports_google_sheets/">'
        'Publicación de Reddit</a></p>'
        '<div class="gd-totals">'
        '<div class="gd-total"><strong>' + str(with_matches) +
        '</strong>ports con referencias</div>'
        '<div class="gd-total"><strong>' + str(official_count) +
        '</strong>con archivos en releases</div>'
        '<div class="gd-total"><strong>' + str(community_count) +
        '</strong>con datos de la hoja</div>'
        '<div class="gd-total"><strong>' + str(report.get("new_matches") or 0) +
        '</strong>nuevas referencias</div></div></div>'
        '<div class="gd-tools"><input class="gd-input" id="gd-search" type="search" '
        'placeholder="Buscar port o Title ID…" autocomplete="off" '
        'aria-label="Buscar port o Title ID">'
        '<select id="gd-filter" aria-label="Filtrar por tipo de evidencia">'
        '<option value="all">Todos con referencias (' + str(with_matches) + ')</option>'
        '<option value="official">Archivo identificado en release (' +
        str(official_count) + ')</option>'
        '<option value="community">Referencia de hoja comunitaria (' +
        str(community_count) + ')</option></select></div>'
        + (('<div class="gd-grid" id="gd-list">' + "".join(parts) + '</div>')
           if parts else empty_text) +
        '<div class="gd-empty-filter" id="gd-empty-filter" hidden>'
        'No hay ports que coincidan con estos filtros.</div>'
        '</section>'
    )


NAV = """
  <nav class="wrap gd-tabs" aria-label="Herramientas de mantenimiento">
    <button class="gd-tab" id="gd-release-tab" type="button"
      aria-selected="true" aria-controls="releases">Actualizaciones Homebrew</button>
    <button class="gd-tab" id="gd-data-tab" type="button"
      aria-selected="false" aria-controls="gd-panel">Búsqueda de Game Data</button>
  </nav>
"""

SCRIPT = """
  <script>
  (function () {
    const releaseTab = document.getElementById('gd-release-tab');
    const dataTab = document.getElementById('gd-data-tab');
    const releases = document.getElementById('releases');
    const toolbar = document.querySelector('.toolbar');
    const panel = document.getElementById('gd-panel');
    function switchTab(data, changeHash) {
      releaseTab.setAttribute('aria-selected', String(!data));
      dataTab.setAttribute('aria-selected', String(data));
      releases.hidden = data;
      toolbar.hidden = data;
      panel.hidden = !data;
      document.body.classList.toggle('gd-active', data);
      if (changeHash) {
        history.replaceState(null, '', data ? '#game-data' : '#releases');
      }
    }
    releaseTab.addEventListener('click', function () { switchTab(false, true); });
    dataTab.addEventListener('click', function () { switchTab(true, true); });
    const search = document.getElementById('gd-search');
    const filter = document.getElementById('gd-filter');
    const cards = Array.from(document.querySelectorAll('.gd-item'));
    const empty = document.getElementById('gd-empty-filter');
    function applyFilter() {
      const q = search.value.trim().toLowerCase();
      const state = filter.value;
      let visible = 0;
      for (const card of cards) {
        const show = (!q || card.dataset.search.includes(q)) &&
                     (state === 'all' || card.dataset.found.split(' ').includes(state));
        card.hidden = !show;
        if (show) visible++;
      }
      empty.hidden = visible !== 0 || cards.length === 0;
    }
    search.addEventListener('input', applyFilter);
    filter.addEventListener('change', applyFilter);
    switchTab(location.hash === '#game-data', false);
  }());
  </script>
"""


def add_data_tab(document, report=None):
    if report is None:
        try:
            report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
    if not isinstance(report, dict):
        report = {}
    for token in ('</style>', '<div class="wrap toolbar">', '<footer class="wrap">',
                  '</body>', '<main class="wrap" id="releases">'):
        if token not in document:
            raise ValueError("Cambió el HTML del dashboard: falta " + token)
    count = sum(bool(a.get("candidates")) for a in report.get("apps", [])
                if isinstance(a, dict))
    navigation = NAV.replace("Búsqueda de Game Data</button>",
                             "Búsqueda de Game Data <span class=\"gd-pill\">" +
                             str(count) + "</span></button>")
    return (document.replace("</style>", STYLE + "\n  </style>", 1)
            .replace('<div class="wrap toolbar">', navigation + '\n  <div class="wrap toolbar">', 1)
            .replace('<footer class="wrap">', render_panel(report) + '\n  <footer class="wrap">', 1)
            .replace('</body>', SCRIPT + '\n</body>', 1))
